"""Coverage and signal, measured on the same 233 meetings the scorecard uses.

Two questions, and the order matters. **Can the column be computed?** first --
a feature that is null for 60% of the sample is not a weak feature, it is a
different feature on a different dataset. **Is it worth anything?** second, and
against a yardstick that is already in the repo: `ff_exp_move_bp`, the fed funds
futures column, whose correlation with the decision is computed here on exactly
the same rows so the comparison is not against a number from a README.

The third thing this file does is show what the leak would have been worth. The
same tone and redline columns are recomputed from the meeting's OWN statement --
the one published at 2 p.m. with the decision -- and correlated with the
decision. That number is the reason this package's lookups are strict. It is
printed so that nobody has to take the docstring's word for it.
"""

from __future__ import annotations

import json
import math
import re

import boardsite as bs
import derive
import lexicon as lx
import repo
import sep as sep_mod
import statements as st

ERAS = [("1997-2007", "1997-01-01", "2008-01-01"),
        ("2008-2015", "2008-01-01", "2016-01-01"),
        ("2016-today", "2016-01-01", "9999-12-31")]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 8:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def partial(r_xy: float | None, r_xz: float | None,
            r_yz: float | None) -> float | None:
    """Correlation of x and y with z removed from both. Ordinary partial r."""
    if r_xy is None or r_xz is None or r_yz is None:
        return None
    den = math.sqrt(max(1e-12, (1 - r_xz ** 2) * (1 - r_yz ** 2)))
    return (r_xy - r_xz * r_yz) / den


def _outcomes() -> dict[str, float]:
    return {d["date"]: d["delta_bp"] for d in repo.decisions()}


def _futures_column() -> dict[str, float]:
    """`ff_exp_move_bp` per meeting, straight out of the built design matrix.

    Read-only, and it is the whole point of the comparison: the yardstick has to
    be the best column the engine already has, scored on the same rows, or the
    text features get graded against nothing.
    """
    path = repo.ENGINE / "data" / "matrix.json"
    if not path.exists():
        return {}
    m = json.loads(path.read_text(encoding="utf-8"))
    cols = m["columns"]
    if "ff_exp_move_bp" not in cols:
        return {}
    i = cols.index("ff_exp_move_bp")
    return {r["date"]: r["x"][i] for r in m["rows"] if r["x"][i] is not None}


def coverage(dates: list[str] | None = None) -> dict:
    """Non-null count per derived column, overall and by era."""
    dates = dates if dates is not None else repo.backtest_dates()
    rows = derive.build(dates)
    out = {"n": len(rows), "columns": {}}
    for col in derive.COLUMNS:
        rec = {"n": sum(1 for r in rows if r.get(col) is not None)}
        for name, lo, hi in ERAS:
            sub = [r for r in rows if lo <= r["date"] < hi]
            rec[name] = "%d/%d" % (sum(1 for r in sub if r.get(col) is not None), len(sub))
        out["columns"][col] = rec
    # The two upstream facts every column inherits.
    out["prior_statement"] = st.coverage(dates)
    out["prior_sep"] = {"n": len(dates),
                        "with_prior_sep": sum(1 for d in dates
                                              if sep_mod.latest_before(d) is not None)}
    # The number that matters more than the raw coverage. Every meeting has *a*
    # prior statement, but before 2000 the Board only issued one when it changed
    # policy, so "the previous statement" can be from a meeting two years back.
    # A stale statement is not the same feature as last meeting's statement, and
    # counting it as covered would be the flattering answer.
    sched = repo.scheduled_dates()
    pos = {d: i for i, d in enumerate(sched)}
    fresh = {name: [0, 0] for name, _, _ in ERAS}
    total = 0
    for d in dates:
        s = st.latest_before(d)
        if not s:
            continue
        i = pos.get(d)
        last = sched[i - 1] if i else None
        ok = last is not None and s.date == last
        total += ok
        for name, lo, hi in ERAS:
            if lo <= d < hi:
                fresh[name][0] += ok
                fresh[name][1] += 1
    out["prior_statement"]["is_the_previous_meeting"] = total
    out["prior_statement"]["by_era"] = {k: "%d/%d" % tuple(v) for k, v in fresh.items()}
    return out


def signal(dates: list[str] | None = None) -> dict:
    """Correlation of each derived column with the decision, and the yardstick."""
    dates = dates if dates is not None else repo.backtest_dates()
    rows = derive.build(dates)
    y = _outcomes()
    ff = _futures_column()

    dy = [y.get(r["date"]) for r in rows]
    # The control that decides whether any of this is new information. The Fed
    # moves in runs -- `repeat the last action` is a 79% baseline in this repo --
    # and the previous statement is, among other things, a description of the
    # previous decision. A text column that correlates with the next decision
    # only because it restates the last one adds nothing to a feature set that
    # already carries `last_dir` and `run_length`. `r_prev` shows how much of
    # each column is that, and `r_partial` is what survives removing it.
    prev = [y.get(r.get("stmt_date")) for r in rows]

    out = {"n": len(rows), "columns": {}}
    for col in derive.COLUMNS:
        xs = [r.get(col) for r in rows]
        moved = [(x, v) for x, v in zip(xs, dy) if v is not None and abs(v) > 0.1]
        r_xy = pearson(xs, dy)
        r_xz = pearson(xs, prev)
        r_yz = pearson(dy, prev)
        out["columns"][col] = {
            "n": sum(1 for x in xs if x is not None),
            "r_delta": _round(r_xy),
            "r_delta_on_moves": _round(pearson([p[0] for p in moved],
                                               [p[1] for p in moved])),
            "r_prev_delta": _round(r_xz),
            "r_partial": _round(partial(r_xy, r_xz, r_yz)),
        }
    fx = [ff.get(r["date"]) for r in rows]
    r_xy, r_xz, r_yz = pearson(fx, dy), pearson(fx, prev), pearson(dy, prev)
    out["yardstick"] = {
        "ff_exp_move_bp": {
            "n": sum(1 for r in rows if r["date"] in ff),
            "r_delta": _round(r_xy),
            "r_prev_delta": _round(r_xz),
            "r_partial": _round(partial(r_xy, r_xz, r_yz)),
        },
        "prev_delta_bp": {"n": sum(1 for p in prev if p is not None),
                          "r_delta": _round(r_yz)},
    }
    out["leak"] = leak_demo(dates)
    return out


def leak_demo(dates: list[str] | None = None) -> dict:
    """What using the meeting's OWN statement would have been worth.

    This is not a feature. It is the measurement that justifies the strict
    inequality in `statements.before()`: the same two columns, computed from the
    statement published at 2 p.m. with the decision instead of the one published
    six weeks earlier. If the second column is not far larger than the first,
    something is wrong with the gate.
    """
    dates = dates if dates is not None else repo.backtest_dates()
    y = _outcomes()
    corpus = st.load()
    by_date = {s.date: s for s in corpus}

    own_tone, own_say, own_diff = [], [], []
    prior_tone, prior_say, prior_diff, dy = [], [], [], []
    for d in dates:
        if d not in y:
            continue
        own = by_date.get(d)
        p1 = st.latest_before(d, corpus=corpus)
        p2 = st.prior(d, 2, corpus=corpus)
        dy.append(y[d])
        own_tone.append(lx.score(bs.words(own.text))["net_per_1k"] if own else None)
        own_say.append(_decision_sentence(own.text) if own else None)
        own_diff.append(derive.redline(own.text, p1.text)["changed_frac"]
                        if own and p1 else None)
        prior_tone.append(lx.score(bs.words(p1.text))["net_per_1k"] if p1 else None)
        prior_say.append(_decision_sentence(p1.text) if p1 else None)
        prior_diff.append(derive.redline(p1.text, p2.text)["changed_frac"]
                          if p1 and p2 else None)
    def hit(says):
        pairs = [(s, v) for s, v in zip(says, dy) if s is not None and v is not None]
        if not pairs:
            return None
        ok = sum(1 for s, v in pairs
                 if s == (0 if abs(v) < 0.1 else (1 if v > 0 else -1)))
        return {"n": len(pairs), "direction_accuracy": round(100.0 * ok / len(pairs), 1)}

    holds = sum(1 for v in dy if v is not None and abs(v) < 0.1)
    return {
        # The headline. Three regexes on the meeting's OWN statement, scored on
        # exactly the metric the scorecard reports. Compare it to the model's
        # published 84.55% direction accuracy and to always-hold at 68.67%.
        "decision_sentence_direction": {"own": hit(own_say), "prior": hit(prior_say),
                                        "always_hold": round(100.0 * holds / len(dy), 1)},
        "decision_sentence_r": {"own": _round(pearson(own_say, dy)),
                                "prior": _round(pearson(prior_say, dy))},
        "lexicon_tone_r": {"own": _round(pearson(own_tone, dy)),
                           "prior": _round(pearson(prior_tone, dy))},
        "redline_r": {"own": _round(pearson(own_diff, dy)),
                      "prior": _round(pearson(prior_diff, dy))},
        "note": "the 'own' column is NOT shipped anywhere. It is the leak, measured.",
    }


_RAISE = re.compile(
    r"(?:decided|voted|agreed|announced)\s+(?:\w+\s+){0,2}to\s+(?:raise|increase|"
    r"tighten)|increase slightly the degree|raise(?:d)? (?:its|the) target|"
    r"tighten the stance|firming of monetary policy", re.I)
_LOWER = re.compile(
    r"(?:decided|voted|agreed|announced)\s+(?:\w+\s+){0,2}to\s+(?:lower|reduce|"
    r"decrease|ease)|decrease slightly the degree|lower(?:ed)? (?:its|the) target|"
    r"ease the stance|easing of monetary policy", re.I)


def _decision_sentence(text: str) -> int:
    """+1 / -1 / 0 from the sentence that announces the action.

    Three regexes and no cleverness. It exists to show the size of the hole:
    a meeting's own statement *says what the Committee did*, so this is not a
    feature, it is the label written in English. Correlating it with the
    decision is the demonstration; shipping it would be the fraud.
    """
    if _RAISE.search(text):
        return 1
    if _LOWER.search(text):
        return -1
    return 0


def _round(x, nd=3):
    return None if x is None else round(x, nd)
