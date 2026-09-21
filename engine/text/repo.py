"""The one place `engine/text/` reaches into the rest of the engine.

This package is self-contained and has its own entry point, but two facts it
needs already exist upstream and re-scraping them would be duplication with a
drift risk: the meeting calendar (`calendar_fed.py`) and the labelled decision
record (`dataset.py` -> `data/decisions.json`). Both are read-only here.

Kept in one file so the coupling is visible and removable, rather than five
modules each quietly adding the parent directory to `sys.path`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))


def meeting_dates() -> set[str]:
    """Every date the Board's calendar calls a meeting (scheduled or not)."""
    import calendar_fed
    return {m["date"] for m in calendar_fed.load()}


def decisions() -> list[dict]:
    """The labelled decision record, oldest first. Empty if it has not been built."""
    path = ENGINE / "data" / "decisions.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["decisions"]


def scheduled_dates() -> list[str]:
    return [d["date"] for d in decisions() if d.get("scheduled")]


def backtest_dates(warmup: int = 60) -> list[str]:
    """The meetings the walk-forward backtest actually scores.

    `backtest.py` trains on the first `warmup` scheduled meetings and scores
    everything after them, which is where the published "233 scheduled
    meetings, 1997-08-19 -> 2026-09-16" comes from. Coverage numbers in this
    package are quoted against exactly that window so they can be compared to
    the scorecard without a translation step.
    """
    return scheduled_dates()[warmup:]
