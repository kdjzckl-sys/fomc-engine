"""Derived features from the PRIOR statement and the PRIOR SEP.

Computed, not wired in. Nothing here touches `features.py` or the design
matrix; the point is to have the columns, measured and coverage-checked, so the
decision to use them is made on evidence rather than on the appeal of the idea.

**Every input is strictly earlier than the meeting it describes.** The lookups
come from `statements.latest_before` and `sep.latest_before`, both of which use
`published < when`, never `<=`. A meeting's own statement and its own SEP print
at 2 p.m. on its own last day, with the decision. There is no version of this
feature set in which they are inputs.

The columns, and why each one is here:

  length and change in length -- the Committee lengthens the statement when it
      is explaining something and shortens it when it is not. The *change* is
      the signal, not the level: 40 words in 1995 and 880 in 2013 is a change in
      communication policy, not in the reaction function.

  balance of risks -- the formal tilt sentence. From February 2000 the Board
      published an explicit one ("the risks are weighted mainly toward
      conditions that may generate heightened inflation pressures"), and desks
      traded it directly. It is the single most decision-shaped piece of
      language in the document, and it is also the one that disappears: see
      README.md for how much of the sample can actually be classified.

  hawkish / dovish lexicon -- a counted dictionary (`lexicon.py`), cited and
      checked in, no model call. Reported honestly in README.md against what it
      is worth, which is less than it looks.

  the redline -- the diff against the previous statement. This is what the
      market actually reads on the day: not the statement, the changes to the
      statement. Word-level add/remove counts and a changed fraction.

  dissents -- count and direction. A dissent is a genuinely strong leading
      indicator: the Committee is path-dependent and a dissenter is usually the
      first vote of the next regime. They are in the statement text, but only
      from March 2002 -- before that the roll call lived in the minutes.

  the dots -- median for the meeting's own year and the next, the change in the
      median for a FIXED target year since the previous SEP, and the dispersion.
      The change is computed year-on-year against the same calendar year,
      because a December SEP's "current year" and a March SEP's "current year"
      are different years and differencing them is nonsense.

  the dot gap -- the prior SEP's median for this year minus the current target
      midpoint: how much more the Committee said it intended to do. Every SEP
      in the record post-dates the move to a target *range*, so the midpoint is
      always `upper - 0.125` and no regime branch is needed.
"""

from __future__ import annotations

import difflib
import re
from datetime import date

import boardsite as bs
import lexicon as lx
import repo
import sep as sep_mod
import statements as st

# -- balance of risks -------------------------------------------------------
#
# Ordered, explicit phrase rules. Four different regimes of tilt language live
# in this corpus and none of them is a paraphrase of another, so each gets its
# own pattern rather than one clever regex that half-matches all four.

_HAWK_RISK = [
    r"risks are weighted mainly toward conditions that may generate heightened inflation",
    r"predominant policy concern remains the risk that inflation",
    r"upside risks? to inflation",
    r"risks?\b[^.]{0,80}\bto the upside",
    r"(?:remains |is |highly )?attentive to inflation risks",
    r"some (?:further )?inflation risks remain",
]
_DOVE_RISK = [
    r"risks are weighted mainly toward conditions that may generate economic weakness",
    # The lookbehind is not decoration. From May 2003 to 2007 the Board's
    # BALANCED formulation was "the upside and downside risks to the attainment
    # of sustainable growth ... are roughly equal", and without it every one of
    # those meetings scored as a dovish tilt -- the single most common tilt
    # sentence in the sample, classified backwards.
    r"(?<!upside and )downside risks?\b[^.]{0,60}(?:outlook|growth|economic|labor)",
    r"risk of inflation becoming undesirably low",
    r"(?<!upside and )risks?\b[^.]{0,80}\bto the downside",
]
_BAL_RISK = [
    r"risks?[^.]{0,80}roughly equal",
    r"risks?[^.]{0,90}(?:roughly|nearly|broadly)\s+(?:in\s+)?balanced?\b",
    r"risks?[^.]{0,80}(?:are|is|remain)\s+balanced\b",
    r"balance of risks",
    r"moving into better balance",
    r"risks to both sides of its dual mandate",
    r"upside and downside risks",
]


def risk_tilt(text: str) -> tuple[int | None, int, int]:
    """(tilt, hawk cues, dove cues). tilt is +1 / 0 / -1, or None if unclassifiable.

    A statement often carries both sides ("downside risks to growth remain, the
    upside risks to inflation have increased" -- August 2008, which is exactly
    the meeting where that was the story). The net of the cue counts is the
    answer; a balanced-language cue with no directional cue reads 0; nothing at
    all reads None and stays null rather than being imputed to "balanced".
    """
    low = " ".join(text.lower().split())
    hawk = sum(len(re.findall(p, low)) for p in _HAWK_RISK)
    dove = sum(len(re.findall(p, low)) for p in _DOVE_RISK)
    bal = sum(len(re.findall(p, low)) for p in _BAL_RISK)
    if hawk == dove == bal == 0:
        return None, hawk, dove
    if hawk == dove:
        return 0, hawk, dove
    return (1 if hawk > dove else -1), hawk, dove


# -- dissents ---------------------------------------------------------------

_VOTING_AGAINST = re.compile(r"voting against[^.]{0,80}?(?:were|was|:)", re.I)
_NONE = re.compile(r"voting against[^.]{0,60}?:?\s*none", re.I)
# "Stephen I. Miran", "Neel Kashkari", "Robert D. McTeer, Jr."
_NAME = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+(?:[A-Z][a-z]+)?\b")
_NOT_A_NAME = {"Voting", "Federal", "Open", "Market", "Committee", "Chair",
               "Vice", "President", "Governor", "Mr", "Ms", "The", "This",
               "Board", "Reserve", "Bank", "New York"}

_DOVE_CUE = re.compile(
    r"prefer\w*\s+(?:a\s+)?(?:to\s+)?(?:lower|reduce|decreas|cut)|"
    r"basis point (?:reduction|cut|decrease)|"
    r"percentage point (?:reduction|cut)|"
    r"prefer\w*[^,.;]{0,40}lower the target|more accommodat", re.I)
_HAWK_CUE = re.compile(
    r"prefer\w*\s+(?:a\s+)?(?:to\s+)?(?:rais|increas|hike)|"
    r"basis point (?:increase|hike)|percentage point increase|"
    r"prefer\w*[^,.;]{0,40}rais(?:e|ing) the target|less accommodat|"
    r"no longer warranted", re.I)
_HOLD_CUE = re.compile(
    r"prefer\w*\s+no change|prefer\w*\s+(?:to\s+)?(?:maintain|leave|keep)|"
    r"unchanged at this meeting", re.I)


def dissents(text: str, committee_delta_bp: float | None) -> dict:
    """Dissent count and direction from a statement's voting paragraph.

    `committee_delta_bp` is what the Committee did **at the meeting this
    statement describes** -- known at publication, so no leak. It is needed
    only for the "preferred no change" dissent, whose sign is defined by what
    it was dissenting from: preferring no change against a cut is hawkish,
    preferring no change against a hike is dovish.

    Returns `n=None` when the statement has no roll call at all, which is every
    statement before March 2002. That is a missing feature, not a zero, and
    encoding it as zero would teach the model that the Greenspan Committee was
    unanimous for eight years.
    """
    if not re.search(r"voting (?:for|against)", text, re.I):
        return {"n": None, "hawk": None, "dove": None, "net": None, "unclassified": None}
    if _NONE.search(text) or not _VOTING_AGAINST.search(text):
        return {"n": 0, "hawk": 0, "dove": 0, "net": 0, "unclassified": 0}

    m = _VOTING_AGAINST.search(text)
    tail = text[m.end():]
    para = tail.split("\n\n", 1)[0]

    spans = [(mm.start(), mm.end()) for mm in _NAME.finditer(para)
             if mm.group(0).split()[0] not in _NOT_A_NAME]
    if not spans:
        return {"n": None, "hawk": None, "dove": None, "net": None, "unclassified": None}

    # Each dissenter owns the text from their name to the next name. A shared
    # clause ("A, B and C, who preferred to raise") leaves the earlier names
    # with no cue of their own, so an unlabelled dissenter inherits from the
    # next labelled one -- which is what the sentence means.
    labels: list[str | None] = []
    for i, (_, e) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else len(para)
        clause = para[e:nxt]
        if _DOVE_CUE.search(clause):
            labels.append("dove")
        elif _HAWK_CUE.search(clause):
            labels.append("hawk")
        elif _HOLD_CUE.search(clause) and committee_delta_bp:
            labels.append("hawk" if committee_delta_bp < 0 else "dove")
        else:
            labels.append(None)
    for i in range(len(labels) - 2, -1, -1):
        if labels[i] is None:
            labels[i] = labels[i + 1]

    hawk = sum(1 for x in labels if x == "hawk")
    dove = sum(1 for x in labels if x == "dove")
    return {"n": len(labels), "hawk": hawk, "dove": dove, "net": hawk - dove,
            "unclassified": sum(1 for x in labels if x is None)}


# -- the redline ------------------------------------------------------------


def redline(new_text: str, old_text: str) -> dict:
    """Word-level diff of a statement against the one before it.

    The market does not read the statement, it reads the changes to the
    statement -- which is why this is the column worth the most of the six and
    the one that needs the least interpretation. `difflib` is stdlib.
    """
    a, b = bs.words(old_text), bs.words(new_text)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    denom = max(len(a) + len(b), 1)
    return {"added": added, "removed": removed,
            "changed_frac": round((added + removed) / denom, 4),
            "similarity": round(sm.ratio(), 4)}


# -- assembly ---------------------------------------------------------------


def _days(a: str, b: str) -> int:
    pa = date(int(a[:4]), int(a[5:7]), int(a[8:10]))
    pb = date(int(b[:4]), int(b[5:7]), int(b[8:10]))
    return (pb - pa).days


def _delta_by_date() -> dict[str, float]:
    return {d["date"]: d["delta_bp"] for d in repo.decisions()}


def _target_by_date() -> list[tuple[str, float]]:
    return [(d["date"], d["target_after"]) for d in repo.decisions()]


def _target_before(path: list[tuple[str, float]], when: str) -> float | None:
    """The target in force going into `when`: the last announced level strictly
    before it. Announced, therefore public, therefore usable."""
    out = None
    for d, v in path:
        if d < when:
            out = v
        else:
            break
    return out


def row(meeting: str, *, corpus=None, seps=None, deltas=None, path=None) -> dict:
    """Every derived text/SEP column for one meeting, all strictly point-in-time."""
    corpus = corpus if corpus is not None else st.load()
    seps = seps if seps is not None else sep_mod.load()
    deltas = deltas if deltas is not None else _delta_by_date()
    path = path if path is not None else _target_by_date()

    f: dict = {"date": meeting}

    s1 = st.latest_before(meeting, corpus=corpus)
    s2 = st.prior(meeting, 2, corpus=corpus)
    s3 = st.prior(meeting, 3, corpus=corpus)

    if s1:
        w1 = bs.words(s1.text)
        f["stmt_date"] = s1.published
        f["stmt_age_days"] = _days(s1.published, meeting)
        f["stmt_words"] = len(w1)
        sc1 = lx.score(w1)
        f["stmt_tone"] = sc1["tone"]
        f["stmt_hawk_net_per_1k"] = sc1["net_per_1k"]
        tilt, hcue, dcue = risk_tilt(s1.text)
        f["stmt_risk_tilt"] = tilt
        f["stmt_risk_hawk_cues"] = hcue
        f["stmt_risk_dove_cues"] = dcue
        d = dissents(s1.text, deltas.get(s1.date))
        f["stmt_dissents"] = d["n"]
        f["stmt_dissent_hawk"] = d["hawk"]
        f["stmt_dissent_dove"] = d["dove"]
        f["stmt_dissent_net"] = d["net"]

        if s2:
            w2 = bs.words(s2.text)
            f["stmt_words_chg"] = len(w1) - len(w2)
            sc2 = lx.score(w2)
            f["stmt_tone_chg"] = (round(sc1["tone"] - sc2["tone"], 4)
                                  if sc1["tone"] is not None and sc2["tone"] is not None
                                  else None)
            t2 = risk_tilt(s2.text)[0]
            f["stmt_risk_tilt_chg"] = (tilt - t2) if (tilt is not None and t2 is not None) else None
            r = redline(s1.text, s2.text)
            f["stmt_diff_added"] = r["added"]
            f["stmt_diff_removed"] = r["removed"]
            f["stmt_diff_changed_frac"] = r["changed_frac"]
            # The change in how much the Committee changed: a statement that
            # was rewritten twice running is a Committee in motion.
            if s3:
                prev = redline(s2.text, s3.text)
                f["stmt_diff_frac_chg"] = round(r["changed_frac"] - prev["changed_frac"], 4)

    p1 = sep_mod.latest_before(meeting, corpus=seps)
    p2 = sep_mod.prior(meeting, 2, corpus=seps)
    if p1:
        y0, y1 = int(meeting[:4]), int(meeting[:4]) + 1
        d0, d1 = sep_mod.horizon(p1, y0), sep_mod.horizon(p1, y1)
        f["sep_date"] = p1.published
        f["sep_age_days"] = _days(p1.published, meeting)
        f["sep_dot_y0"] = sep_mod.median(d0)
        f["sep_dot_y1"] = sep_mod.median(d1)
        f["sep_disp_y0"] = (round(sep_mod.dispersion(d0), 4)
                            if sep_mod.dispersion(d0) is not None else None)
        f["sep_disp_y1"] = (round(sep_mod.dispersion(d1), 4)
                            if sep_mod.dispersion(d1) is not None else None)
        f["sep_n"] = len(d0) or None
        if p2:
            # Same target year on both sides, or the difference is meaningless.
            for tag, yr in (("y0", y0), ("y1", y1)):
                a = sep_mod.median(sep_mod.horizon(p1, yr))
                b = sep_mod.median(sep_mod.horizon(p2, yr))
                f["sep_dot_%s_chg" % tag] = (round(a - b, 4)
                                             if a is not None and b is not None else None)
        tgt = _target_before(path, meeting)
        if tgt is not None and f["sep_dot_y0"] is not None:
            # Every SEP in the record post-dates December 2008, so the policy
            # setting is always a range and its midpoint is upper - 0.125.
            f["sep_dot_minus_mid"] = round(f["sep_dot_y0"] - (tgt - 0.125), 4)

    return f


COLUMNS = [
    "stmt_age_days", "stmt_words", "stmt_words_chg",
    "stmt_tone", "stmt_tone_chg", "stmt_hawk_net_per_1k",
    "stmt_risk_tilt", "stmt_risk_tilt_chg",
    "stmt_diff_added", "stmt_diff_removed", "stmt_diff_changed_frac",
    "stmt_diff_frac_chg",
    "stmt_dissents", "stmt_dissent_hawk", "stmt_dissent_dove", "stmt_dissent_net",
    "sep_age_days", "sep_dot_y0", "sep_dot_y1", "sep_dot_y0_chg", "sep_dot_y1_chg",
    "sep_disp_y0", "sep_disp_y1", "sep_dot_minus_mid",
]


def build(dates: list[str] | None = None) -> list[dict]:
    """One row per meeting. Default: exactly the 233 the backtest scores."""
    dates = dates if dates is not None else repo.backtest_dates()
    corpus, seps = st.load(), sep_mod.load()
    deltas, path = _delta_by_date(), _target_by_date()
    return [row(d, corpus=corpus, seps=seps, deltas=deltas, path=path) for d in dates]


if __name__ == "__main__":
    import json
    import sys
    rows = build()
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows[-4:]:
            print(json.dumps(r, indent=1))
        print("\n%d rows, %s -> %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))
