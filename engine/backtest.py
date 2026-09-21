"""Walk-forward validation. Every number this project reports comes from here.

The rule: to predict meeting i, the model may use meetings 0..i-1 and nothing
else. Refit from scratch at each step -- scaler, imputation medians, cutpoints,
coefficients. That is slower than one fit and a rolling score, and it is the only
version that answers the question anyone cares about, which is "would this have
been right at the time", not "can it describe what happened".

Combined with the vintage data in fred.py, the two together close both leakage
channels: the model never sees a future meeting, and it never sees a revision
that had not printed. Most published Fed-prediction accuracy numbers quietly
violate one or the other and are 10-20 points too high as a result.

Four baselines, because a 75% accuracy headline is meaningless without them:

  always-hold     The Fed holds ~71% of scheduled meetings. Anything that cannot
                  beat this is not a model, it is a coin that says "hold".
  persistence     Repeat the last meeting's action. Policy moves in runs, so this
                  is stronger than it sounds.
  market proxy    Sign of (1y Treasury - effective funds). A yield spread
                  standing in for the instrument the market actually prices
                  decisions in.
  market futures  The CME ZQ strip run through the FedWatch arithmetic
                  (futures.py) -- what the market had genuinely priced going
                  into the meeting. This is the honest bar. The proxy is kept
                  beside it rather than replaced by it, so the two can be read
                  against each other and the substitution stays visible.

THE MOVE SUBSET IS PUBLISHED HERE AND NOWHERE ELSE. All-meeting accuracy is
dominated by holds, so the figure a desk asks for first is how the model does on
the meetings where the Committee actually moved. That number used to be derived
three times -- here, again in the dashboard's JavaScript, and a third time in a
downstream consumer -- and three derivations of one number drift. `score()` now
emits the whole table, every baseline recomputed on the move subset, and every
consumer reads it rather than rebuilding it.

Recall and its price are carried in the SAME record, deliberately. The curve
proxy calls direction on 91.8% of moves, which reads as a better forecaster than
this model until you see it buys that by crying "move" at 45.6% of holds. A
format that lets one of those two numbers be read without the other is a format
that will eventually be misread, so there is no such format here.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matrix as mx
import model as M

import features

HERE = Path(__file__).resolve().parent
OUT = HERE / "models" / ("backtest.json" if features.FUTURES
                         else "backtest.noff.json")

# The futures baseline is scored in BOTH builds, including --no-futures. A
# baseline is a property of the meetings, not of the feature set the model was
# handed, and holding it identical across the two runs is exactly what makes
# "do futures help the model" a fair question -- the same check the README
# already makes with the other three baselines.
#
# The band is half the smallest increment the Committee uses. Under the
# two-outcome reading the FedWatch arithmetic implies, more than 12.5bp priced
# into a 25bp decision is more than a coin flip on that decision, which is the
# point at which the strip is calling a move. It is set on that reasoning and it
# is NOT tuned: no threshold was searched, and the result it produces is not one
# that flatters this model.
FUTURES_BAND_BP = 12.5


def _prep(rows, include_unscheduled, include_zlb):
    out = []
    for r in rows:
        if not include_unscheduled and not r["scheduled"]:
            continue
        if not include_zlb and r["zlb"] and r["label"] == 0:
            continue
        out.append(r)
    return out


def _priced_move(meeting_date: str, start: str) -> tuple:
    """(bp the futures strip priced for this meeting, source), point-in-time.

    Same discipline as the feature column in features.py, and the same two dates:
    the strip is read as of the evening before the meeting opens, and the
    FedWatch weighting is anchored on the day the decision takes effect, which is
    the day after the announcement. A quote cannot be visible to a meeting that
    opened before it printed -- futures.py enforces that inside the lookup.

    `source` is 'futures' only where the strip genuinely reached this meeting.
    Where it did not, features.py substitutes the DGS3MO-EFFR proxy; a BASELINE
    must not do that silently, so the source comes back with the number and the
    caller scores those meetings as uncovered rather than quietly grading a
    different series under the futures heading.
    """
    import fred
    import futures

    return futures.expected_move_bp(fred.shift_days(start, -1),
                                    fred.shift_days(meeting_date, 1))


def run(min_train: int = 60, l2: float = 2.0, half_life: float = 12.0,
        epochs: int = 120, include_unscheduled: bool = False,
        include_zlb: bool = True, start: str | None = None,
        cold_every: int = 24, use: list[str] | None = None,
        verbose: bool = True) -> dict:
    """Refit and predict, one meeting at a time, forward through history.

    `use` restricts the feature set to columns whose name starts with one of the
    given prefixes -- that is how the ablations in tune.py isolate what the macro
    block adds on top of what the market had already priced.
    """
    data = mx.load()
    cols = data["columns"]
    keep = list(range(len(cols)))
    if use:
        keep = [j for j, c in enumerate(cols) if any(c.startswith(u) for u in use)]
        if not keep:
            raise SystemExit("no features matched: " + ",".join(use))
    rows = _prep(data["rows"], include_unscheduled, include_zlb)
    n_feat = len(keep)
    i_mkt1y = cols.index("mkt_1y")

    # Meeting start dates for the futures baseline. The design matrix stores the
    # announcement date only, and the strip has to be read as of the day before
    # the meeting OPENS -- a different date at every two-day meeting.
    starts: dict[str, str] = {}
    ff_live = True
    try:
        import dataset
        import futures
        starts = {r["date"]: r["start"] for r in dataset.load()}
        futures.load()
    except Exception as e:  # noqa: BLE001 - upstream source; degrade loudly
        ff_live = False
        print("  backtest: futures baseline unavailable (%s); it will report"
              " zero coverage rather than fall back to the proxy" % e)

    preds = []
    m = M.OrderedLogit(n_feat, l2=l2)
    for i in range(min_train, len(rows)):
        if start and rows[i]["date"] < start:
            continue
        train = rows[:i]
        X_raw = [[r["x"][j] for j in keep] for r in train]
        sc = mx.Scaler(n_feat).fit(X_raw)
        X = [sc.transform(x) for x in X_raw]
        y = [r["label"] for r in train]
        w = M.recency_weights([r["date"] for r in train], half_life)

        # Cold restart periodically so a warm chain cannot drift into a bad
        # basin and stay there for the rest of the sample.
        cold = (not preds) or (cold_every and len(preds) % cold_every == 0)
        m.l2 = l2
        m.fit(X, y, w, epochs=epochs * (4 if cold else 1), warm=not cold)
        p = m.proba(sc.transform([rows[i]["x"][j] for j in keep]))

        ff_bp, ff_src = None, "none"
        if ff_live and rows[i]["date"] in starts:
            try:
                ff_bp, ff_src = _priced_move(rows[i]["date"], starts[rows[i]["date"]])
            except Exception:  # noqa: BLE001 - one meeting, not the whole run
                ff_bp, ff_src = None, "none"

        preds.append({
            "date": rows[i]["date"],
            "actual": rows[i]["label"],
            "actual_bp": rows[i]["delta_bp"],
            "probs": p,
            "pred": M.LABELS[p.index(max(p))],
            "exp_bp": M.expected_bp(p),
            "zlb": rows[i]["zlb"],
            # baselines, computed on the same row
            "mkt_1y": rows[i]["x"][i_mkt1y],
            "prev_label": rows[i - 1]["label"],
            # what the fed funds futures strip had actually priced, and whether
            # it reached this meeting at all. 'proxy' and 'none' both mean the
            # futures baseline does not cover this row.
            "ff_priced_bp": ff_bp,
            "ff_source": ff_src,
        })
        if verbose and len(preds) % 25 == 0:
            print("  ...%d meetings scored (through %s)" % (len(preds), rows[i]["date"]))

    return score(preds, verbose=verbose)


# -- the callers ------------------------------------------------------------
# One function per row of the scorecard: meeting -> directional call, or None
# where that caller does not cover the meeting at all. None is not "hold" -- a
# baseline with no data has to be left out of its own denominator, not scored as
# though it had said something.


def _dir3(lbl) -> int:
    return -1 if lbl < 0 else (1 if lbl > 0 else 0)


def _mkt_call(mkt_1y):
    """Direction the 1y-vs-funds spread implies. +-15bp deadband: the 1y sits a
    little above funds in normal times (term premium), so a small positive
    spread is not a hike signal."""
    if mkt_1y is None:
        return 0
    if mkt_1y > 0.15:
        return 1
    if mkt_1y < -0.15:
        return -1
    return 0


def _futures_call(p):
    """Direction the ZQ strip priced, or None where the strip did not reach.

    Deliberately no fallback. features.py substitutes the Treasury proxy for the
    37 late-month meetings the strip cannot price, which is right for a feature
    -- the model needs a number in every cell. It is wrong for a baseline: a
    column headed "fed funds futures" that is quietly part Treasury spread is
    the kind of thing this repo exists not to publish. Those meetings are
    uncovered, and the coverage count travels next to the score.
    """
    if p.get("ff_source") != "futures":
        return None
    bp = p.get("ff_priced_bp")
    if bp is None:
        return None
    if bp > FUTURES_BAND_BP:
        return 1
    if bp < -FUTURES_BAND_BP:
        return -1
    return 0


CALLERS = [
    ("model", "this model",
     lambda p: _dir3(p["pred"]), True),
    ("always_hold", "always say hold",
     lambda p: 0, False),
    ("repeat_last", "repeat the last action",
     lambda p: _dir3(p.get("prev_label", 0)), False),
    ("market_proxy", "market-implied (1y - funds proxy)",
     lambda p: _mkt_call(p.get("mkt_1y")), False),
    ("market_futures", "market-implied (fed funds futures)",
     _futures_call, False),
]


def _share(rows, fn) -> float | None:
    if not rows:
        return None
    return sum(1 for r in rows if fn(r)) / len(rows)


def subset_rows(preds: list[dict]) -> list[dict]:
    """The whole scorecard: every caller, on all meetings and on the move subset.

    Each record carries its coverage, its all-meeting direction accuracy, its
    recall on moves AND the false-alarm rate on holds that recall cost. Those
    last two sit in one object on purpose -- see the module docstring. There is
    no accessor here that hands back one without the other.
    """
    out = []
    for key, label, fn, exact in CALLERS:
        cov, moves, holds = [], [], []
        for p in preds:
            try:
                call = fn(p)
            except Exception:  # noqa: BLE001 - a malformed row is uncovered, not fatal
                call = None
            if call is None:
                continue
            cov.append((p, call))
            (moves if p["actual"] != 0 else holds).append((p, call))
        out.append({
            "key": key,
            "label": label,
            # Coverage, stated rather than implied: the futures strip reaches
            # 196 of 233 meetings, and a score over a different denominator has
            # to say so or it will be quoted as though it were the whole record.
            "n": len(cov),
            "n_moves": len(moves),
            "n_holds": len(holds),
            "all_direction": _share(cov, lambda t: t[1] == _dir3(t[0]["actual"])),
            # recall: of the meetings where the Fed moved, how many it called
            "move_direction": _share(moves, lambda t: t[1] == _dir3(t[0]["actual"])),
            # the price of that recall: of the meetings where it held, how many
            # this caller shouted "move" at
            "hold_false_alarm": _share(holds, lambda t: t[1] != 0),
            # exact 25/50bp size, which only a 5-class predictor can call
            "move_5class": (_share(moves, lambda t: t[0]["pred"] == t[0]["actual"])
                            if exact else None),
        })
    return out


def score(preds: list[dict], verbose: bool = True) -> dict:
    n = len(preds)
    if n == 0:
        raise SystemExit("no predictions -- min_train larger than the sample?")

    dir3 = _dir3

    hit = sum(1 for p in preds if p["pred"] == p["actual"])
    hit3 = sum(1 for p in preds if dir3(p["pred"]) == dir3(p["actual"]))
    ll = sum(M.log_loss(p["probs"], p["actual"]) for p in preds) / n
    br = sum(M.brier(p["probs"], p["actual"]) for p in preds) / n
    mae = sum(abs(p["exp_bp"] - p["actual_bp"]) for p in preds) / n

    # baselines
    base_hold = sum(1 for p in preds if p["actual"] == 0) / n
    mkt = sum(1 for p in preds
              if dir3(_mkt_call(p["mkt_1y"])) == dir3(p["actual"])) / n
    persist = sum(1 for p in preds if dir3(p["prev_label"]) == dir3(p["actual"])) / n

    # move-only: how it does when the Fed actually moved (the hard subset)
    moves = [p for p in preds if p["actual"] != 0]
    move_hit = (sum(1 for p in moves if p["pred"] == p["actual"]) / len(moves)) if moves else 0.0
    move_dir = (sum(1 for p in moves if dir3(p["pred"]) == dir3(p["actual"])) / len(moves)) if moves else 0.0
    # and the false-alarm rate: called a move when it held
    holds = [p for p in preds if p["actual"] == 0]
    false_alarm = (sum(1 for p in holds if p["pred"] != 0) / len(holds)) if holds else 0.0

    rows = subset_rows(preds)
    by_key = {r["key"]: r for r in rows}
    ff = by_key["market_futures"]
    # The futures baseline covers fewer meetings than the others, so the table
    # above is not like-for-like against it. Rather than leave that for a reader
    # to notice, every caller is rescored on exactly the meetings the strip
    # reached, in the same shape.
    covered = [p for p in preds if _futures_call(p) is not None]

    res = {
        "n": n, "from": preds[0]["date"], "to": preds[-1]["date"],
        "accuracy_5class": hit / n,
        "accuracy_direction": hit3 / n,
        "log_loss": ll, "brier": br, "mae_bp": mae,
        "baseline_always_hold": base_hold,
        "baseline_market_direction": mkt,
        "baseline_persistence": persist,
        # The real futures baseline, scored only where the strip reaches, with
        # its coverage travelling in the same artifact.
        "baseline_futures_direction": ff["all_direction"],
        "baseline_futures_n": ff["n"],
        "baseline_futures_band_bp": FUTURES_BAND_BP,
        "n_moves": len(moves),
        "move_accuracy_5class": move_hit,
        "move_accuracy_direction": move_dir,
        "false_alarm_rate": false_alarm,
        # THE CANONICAL MOVE SUBSET. Every consumer reads this; nothing
        # rederives it. Recall and the false alarms that bought it sit in one
        # record per caller, so neither a reader nor a loader that picks fields
        # can end up with one and not the other.
        "move_subset": {
            "n": n,
            "n_moves": len(moves),
            "n_holds": len(holds),
            "futures_band_bp": FUTURES_BAND_BP,
            "note": ("Recall on moves and false alarms on holds are one record "
                     "per caller because either alone misleads: the curve proxy "
                     "calls %.1f%% of moves and pays for it by crying 'move' at "
                     "%.1f%% of holds."
                     % (100 * (by_key["market_proxy"]["move_direction"] or 0.0),
                        100 * (by_key["market_proxy"]["hold_false_alarm"] or 0.0))),
            "rows": rows,
            "like_for_like": ({
                "basis": "market_futures",
                "n": len(covered),
                "n_moves": sum(1 for p in covered if p["actual"] != 0),
                "n_holds": sum(1 for p in covered if p["actual"] == 0),
                "note": ("Every caller rescored on exactly the meetings the "
                         "futures strip reaches, so the futures column is "
                         "compared against the others on identical rows."),
                "rows": subset_rows(covered),
            } if covered else None),
        },
        "calibration": calibration(preds),
        "preds": preds,
    }

    if verbose:
        report(res)
    return res


def calibration(preds: list[dict], bins: int = 5) -> list[dict]:
    """Reliability of P(move). If the model says 30%, does it happen 30% of the time?

    This matters more than accuracy for the way the output is actually used: a
    probability you can size against is worth more than a label you cannot.
    """
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [p for p in preds if lo <= (1 - p["probs"][2]) < hi
               or (b == bins - 1 and (1 - p["probs"][2]) == 1.0)]
        if not sel:
            out.append({"bin": "%.0f-%.0f%%" % (lo * 100, hi * 100), "n": 0,
                        "predicted": None, "actual": None})
            continue
        out.append({
            "bin": "%.0f-%.0f%%" % (lo * 100, hi * 100),
            "n": len(sel),
            "predicted": sum(1 - p["probs"][2] for p in sel) / len(sel),
            "actual": sum(1 for p in sel if p["actual"] != 0) / len(sel),
        })
    return out


def _pct(x) -> str:
    return "   n/a" if x is None else "%5.1f%%" % (100 * x)


def _subset_table(rows: list[dict], indent: str = "  ") -> None:
    print("%s%-36s %9s %8s %9s %12s"
          % (indent, "direction called by", "scored on", "all", "on moves",
             "false alarm"))
    for r in rows:
        print("%s%-36s %9d %8s %9s %12s"
              % (indent, r["label"], r["n"], _pct(r["all_direction"]),
                 _pct(r["move_direction"]), _pct(r["hold_false_alarm"])))


def report(r: dict) -> None:
    print("\n" + "=" * 80)
    print("WALK-FORWARD BACKTEST  %s -> %s   (%d meetings)" % (r["from"], r["to"], r["n"]))
    print("=" * 80)
    print("  5-class accuracy        %.1f%%" % (100 * r["accuracy_5class"]))
    print("  direction accuracy      %.1f%%   (cut / hold / hike)" % (100 * r["accuracy_direction"]))
    print("  mean |error|            %.1f bp" % r["mae_bp"])
    print("  log loss                %.3f" % r["log_loss"])
    print("  Brier                   %.3f" % r["brier"])

    ms = r.get("move_subset") or {}
    print("\n  -- the hard subset: recall and its price, never one without the other --")
    print("  %d moves / %d holds of %d scored meetings. 'on moves' is recall;"
          " 'false alarm'"
          % (ms.get("n_moves", r["n_moves"]), ms.get("n_holds", 0), r["n"]))
    print("  is what that recall cost on the holds. Reading either alone is the"
          " misread.\n")
    _subset_table(ms.get("rows", []))
    print("\n    exact 25/50bp size on moves    model %s   (the baselines call"
          " direction only)" % _pct(r["move_accuracy_5class"]))
    print("    futures band: a move counts as priced past +-%.1fbp -- half a"
          " 25bp increment, not a tuned threshold"
          % r.get("baseline_futures_band_bp", FUTURES_BAND_BP))

    lfl = ms.get("like_for_like")
    if lfl:
        print("\n  -- like-for-like: the %d meetings the futures strip reaches"
              " (%d moves / %d holds) --" % (lfl["n"], lfl["n_moves"], lfl["n_holds"]))
        _subset_table(lfl["rows"])

    print("\n  -- calibration of P(move) --")
    for b in r["calibration"]:
        if not b["n"]:
            continue
        print("    said %-8s n=%3d   predicted %4.0f%%   happened %4.0f%%"
              % (b["bin"], b["n"], 100 * b["predicted"], 100 * b["actual"]))


def confusion(preds: list[dict]) -> None:
    print("\n  actual \\ predicted")
    print("           " + "".join("%8s" % n for n in M.NAMES))
    for a in M.LABELS:
        row = Counter(p["pred"] for p in preds if p["actual"] == a)
        print("  %-8s " % M.NAMES[M.LABELS.index(a)]
              + "".join("%8d" % row.get(pl, 0) for pl in M.LABELS))


def worst(preds: list[dict], k: int = 10) -> None:
    print("\n  worst misses (by |expected - actual| bp):")
    for p in sorted(preds, key=lambda p: abs(p["exp_bp"] - p["actual_bp"]), reverse=True)[:k]:
        print("    %s  actual %+6.1fbp   model said %+6.1fbp   P(hold)=%.0f%%"
              % (p["date"], p["actual_bp"], p["exp_bp"], 100 * p["probs"][2]))


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    def opt(name, default, cast=float):
        for a in args:
            if a.startswith("--" + name + "="):
                return cast(a.split("=", 1)[1])
        return default

    r = run(min_train=int(opt("min-train", 60)),
            l2=opt("l2", 2.0),
            half_life=opt("half-life", 12.0),
            epochs=int(opt("epochs", 400)),
            include_unscheduled="--with-unscheduled" in args,
            start=opt("start", None, str))
    confusion(r["preds"])
    worst(r["preds"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(r, indent=1), encoding="utf-8")
    print("\n  written: %s" % OUT)
