"""The labelled decision history: what the Committee actually did, meeting by meeting.

The label is *derived*, never typed. FRED carries the policy target as a daily
series -- DFEDTAR (a single target, 1982-09-27 to 2008-12-15) then DFEDTARU (the
upper bound of the range, 2008-12-16 onward). Splice them and every change in
that series is a policy action, dated to the day it took effect. Join those
changes to the meeting calendar and you have the full record with no hand-entry
and no memory involved.

Three details that decide whether the labels are right:

* **Announcement vs. effective date.** The target usually moves the day after the
  statement, occasionally the same day. So the decision at meeting T is read as
  (level a few days after T) minus (level the day before T), with the window
  clipped so it can never run into the next meeting.
* **Unscheduled actions.** 1998, 2001, 2008 and 2020 all contain intermeeting
  cuts. They appear in the calendar as conference calls / "(unscheduled)" and are
  labelled the same way. They are kept in the history but excluded from training
  by default -- an inter-meeting emergency cut is a different data-generating
  process from a scheduled decision, and mixing them teaches the model nothing
  useful about the next scheduled meeting.
* **Multi-session meetings.** The Board occasionally files one meeting under two
  panels (September 2003). Scheduled meetings one day apart collapse to the later
  date, which is the day the decision landed.

Bucketing: the label is an *ordered* class, because the Committee's choice set is
ordered. cut50+ < cut25 < hold < hike25 < hike50+. Nothing here is a model yet.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import calendar_fed
import fred

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "decisions.json"

# Ordered class labels. The integer IS the order -- the model relies on it.
CLASSES = [
    (-2, "cut50+"),
    (-1, "cut25"),
    (0, "hold"),
    (1, "hike25"),
    (2, "hike50+"),
]
CLASS_NAME = dict(CLASSES)
# Representative basis points per class, for the expected-move calculation.
CLASS_BP = {-2: -62.5, -1: -25.0, 0: 0.0, 1: 25.0, 2: 62.5}


def policy_path(refresh: bool = False) -> list[fred.Obs]:
    """The spliced daily policy target, oldest first.

    DFEDTAR is the pre-2008 single target; DFEDTARU is the upper bound of the
    range that replaced it. The target is an announcement, not an estimate, so
    it is never revised -- vintage mode is unnecessary and would be wrong.
    """
    old = fred.series("DFEDTAR", vintage=False, refresh=refresh, start="1982-09-27")
    new = fred.series("DFEDTARU", vintage=False, refresh=refresh, start="2008-12-16")
    cut = new[0].date if new else "9999-12-31"
    return [o for o in old if o.date < cut] + new


def _level_on(path: list[fred.Obs], when: str) -> float | None:
    """Target in force on `when` (last observation at or before it)."""
    v = None
    for o in path:
        if o.date <= when:
            v = o.value
        else:
            break
    return v


def _dedupe(meetings: list[dict]) -> list[dict]:
    """Collapse scheduled meetings filed one day apart onto the later date."""
    out: list[dict] = []
    for m in meetings:
        if out and m["scheduled"] and out[-1]["scheduled"]:
            gap = (date.fromisoformat(m["date"]) - date.fromisoformat(out[-1]["date"])).days
            if gap <= 1:
                m = dict(m, start=out[-1]["start"])
                out[-1] = m
                continue
        out.append(m)
    return out


def bucket(delta_bp: float) -> int:
    """Signed basis-point move -> ordered class."""
    if delta_bp <= -37.5:
        return -2
    if delta_bp <= -12.5:
        return -1
    if delta_bp < 12.5:
        return 0
    if delta_bp < 37.5:
        return 1
    return 2


def build(refresh: bool = False, first: str = "1990-01-01") -> list[dict]:
    """Label every meeting in the calendar with the action it produced."""
    path = policy_path(refresh=refresh)
    meetings = _dedupe([m for m in calendar_fed.load() if m["date"] >= first])
    today = date.today().isoformat()

    rows: list[dict] = []
    for i, m in enumerate(meetings):
        d = m["date"]
        if d > today:
            continue                      # not yet held -- that is predict.py's job

        before = _level_on(path, fred.shift_days(m["start"], -1))
        # The move lands within a few days of the statement. Clip the window so
        # it can never absorb the *next* meeting's action.
        horizon = fred.shift_days(d, 6)
        nxt = meetings[i + 1]["date"] if i + 1 < len(meetings) else None
        if nxt:
            horizon = min(horizon, fred.shift_days(nxt, -1))
        after = _level_on(path, horizon)

        if before is None or after is None:
            continue                      # before the target series begins

        delta_bp = round((after - before) * 100, 1)
        rows.append({
            "date": d,
            "start": m["start"],
            "scheduled": m["scheduled"],
            "sep": m["sep"],
            "days": m["days"],
            "target_before": before,
            "target_after": after,
            "delta_bp": delta_bp,
            "label": bucket(delta_bp),
            "label_name": CLASS_NAME[bucket(delta_bp)],
            # At the zero lower bound the Committee's choice set is truncated --
            # it cannot cut. Flagged so the model can condition on it and so the
            # scorecard can report with and without those meetings.
            "zlb": before <= 0.30,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"built": datetime.now().isoformat(timespec="seconds"),
         "n": len(rows), "decisions": rows}, indent=1), encoding="utf-8")
    return rows


def load(auto: bool = True) -> list[dict]:
    if not OUT.exists():
        if not auto:
            raise SystemExit("no decision history -- run: python cli.py build")
        return build()
    return json.loads(OUT.read_text(encoding="utf-8"))["decisions"]


if __name__ == "__main__":
    import collections
    import sys

    rows = build(refresh="--refresh" in sys.argv)
    sched = [r for r in rows if r["scheduled"]]
    print("%d meetings labelled, %s -> %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    print("scheduled=%d  unscheduled=%d  at-ZLB=%d"
          % (len(sched), len(rows) - len(sched), sum(1 for r in rows if r["zlb"])))
    c = collections.Counter(r["label_name"] for r in sched)
    for _, name in CLASSES:
        print("  %-7s %3d  (%4.1f%%)" % (name, c[name], 100 * c[name] / len(sched)))
    print("\nlargest moves:")
    for r in sorted(rows, key=lambda r: abs(r["delta_bp"]), reverse=True)[:8]:
        print("  %s %+7.1fbp  %.2f -> %.2f  %s"
              % (r["date"], r["delta_bp"], r["target_before"], r["target_after"],
                 "scheduled" if r["scheduled"] else "UNSCHEDULED"))
