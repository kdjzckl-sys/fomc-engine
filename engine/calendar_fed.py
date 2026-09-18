"""The FOMC meeting calendar, scraped from federalreserve.gov.

Why scrape instead of hardcoding: a hand-typed list of 250 meeting dates across
30 years is a fabrication risk and rots every January. The Board publishes the
calendar in a stable, structured block (`fomc-meeting__month` /
`fomc-meeting__date`), and the historical-year pages use the same markup. So the
dates come from the record, not from memory.

Two pages matter:
  * fomccalendars.htm        -- the last ~5 years plus every future scheduled date
  * fomchistorical{YYYY}.htm -- one page per year back to 1936

What we keep per meeting: the END date (the decision lands on the last day of a
two-day meeting), whether it was a projection/SEP meeting (the `*` marker), and
whether a press conference was held. Unscheduled inter-meeting actions do not
appear here -- those are recovered from the target series in dataset.py, which
is the honest place for them since the calendar never anticipated them.

Cached to data/fomc_meetings.json. If a fetch fails the cache is used and the
staleness is reported; nothing is invented.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "fomc_meetings.json"

CAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
HIST_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical%d.htm"

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]
# The Board writes month-spanning meetings both ways: "April/May" on the
# historical pages, "Jan/Feb" and "Oct/Nov" on the rolling calendar. Key on the
# first three letters so both forms resolve.
MONTHS = {m[:3].lower(): i + 1 for i, m in enumerate(_MONTH_NAMES)}


def month_num(name: str) -> int | None:
    return MONTHS.get(name.strip()[:3].lower())


# Entries the Board files under a meeting panel that were not a policy meeting.
NON_MEETING = ("notation vote", "cancelled", "canceled")

# The panel that opens each year block.
YEAR_RE = re.compile(r">(\d{4}) FOMC Meetings")
ROW_RE = re.compile(
    r'fomc-meeting__month[^>]*>(?:<strong>)?\s*([A-Za-z/\s]+?)\s*(?:</strong>)?\s*</div>'
    r'\s*<div[^>]*fomc-meeting__date[^>]*>\s*(.*?)\s*</div>',
    re.S)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 jarvis-fomc/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("could not fetch " + url)


def _parse_year_block(year: int, block: str) -> list[dict]:
    """Rows inside one year's panel -> meeting records."""
    out: list[dict] = []
    for month_txt, day_txt in ROW_RE.findall(block):
        clean_day = re.sub(r"<[^>]+>", "", day_txt).replace("&nbsp;", " ")
        if any(tok in clean_day.lower() for tok in NON_MEETING):
            continue                        # a poll or a meeting that never convened
        # The Board marks SEP / projection meetings with an asterisk.
        is_sep = "*" in clean_day
        clean_day = clean_day.replace("*", "").strip()
        if not clean_day or not re.search(r"\d", clean_day):
            continue

        months = [month_num(m) for m in
                  re.sub(r"<[^>]+>", "", month_txt).replace("&nbsp;", " ").split("/")]
        months = [m for m in months if m]
        if not months:
            continue

        # Day text is "28", "27-28", "31-1" (month-spanning), or "17-18".
        days = [int(d) for d in re.findall(r"\d+", clean_day)]
        if not days:
            continue
        end_day = days[-1]
        # A spanning meeting ("Jan/Feb", "31-1") ends in the 2nd month; a
        # December/January pair rolls the year forward.
        end_month = months[-1]
        end_year = year + 1 if len(months) > 1 and months[-1] < months[0] else year
        try:
            end = date(end_year, end_month, end_day)
        except ValueError:
            continue

        try:
            start = date(year, months[0], days[0])
        except ValueError:
            start = end

        out.append({
            "date": end.isoformat(),          # decision date == last day
            "start": start.isoformat(),
            "scheduled": True,
            "sep": is_sep,
            "days": (end - start).days + 1,
        })
    return out


def _parse_page(html: str) -> list[dict]:
    """Split a page into year panels and parse each."""
    marks = [(m.start(), int(m.group(1))) for m in YEAR_RE.finditer(html)]
    out: list[dict] = []
    for i, (pos, year) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(html)
        out.extend(_parse_year_block(year, html[pos:end]))
    return out


# -- historical year pages --------------------------------------------------
#
# Pre-2021 years use a different layout: one panel per event, titled in prose.
# Observed forms, all handled below:
#   "February 3-4 Meeting - 1994"        two-day scheduled meeting
#   "March 22 Meeting - 1994"            one-day scheduled meeting
#   "April/May 30-1 Meeting - 2019"      month-spanning
#   "January 21 Conference Call - 2008"  unscheduled call (the 75bp intermeeting cut)
#   "October 4 (unscheduled) - 2019"

#   "March 2 (unscheduled) Meeting - 2020"   emergency 50bp cut
#   "March 17-18 (cancelled) Meeting - 2020" superseded by the Mar 15 action
#   "March 19 (notation vote) - 2020"        a vote on the record, not a meeting

HEAD_RE = re.compile(r"panel-heading[^>]*>(.{0,160}?)</h5>", re.S)
TITLE_RE = re.compile(
    r"^([A-Za-z]+(?:/[A-Za-z]+)?)\s+([\d–\-]+(?:\s*[–-]\s*\d+)?)\s*"
    r"(.*?)\s*-\s*(\d{4})\s*$")


def _parse_historical(html: str) -> list[dict]:
    out: list[dict] = []
    for raw in HEAD_RE.findall(html):
        title = re.sub(r"<[^>]+>", "", raw).replace("&nbsp;", " ")
        title = re.sub(r"\s+", " ", title).strip()
        m = TITLE_RE.match(title)
        if not m:
            continue
        month_txt, day_txt, kind_txt, year_txt = m.groups()
        kind_txt = kind_txt.lower()
        # A notation vote is a poll conducted on the record between meetings; a
        # cancelled meeting never convened. Neither is a decision event.
        if any(tok in kind_txt for tok in NON_MEETING):
            continue
        scheduled = ("conference call" not in kind_txt
                     and "unscheduled" not in kind_txt)
        year = int(year_txt)
        months = [month_num(x) for x in month_txt.split("/")]
        months = [m for m in months if m]
        if not months:
            continue
        days = [int(d) for d in re.findall(r"\d+", day_txt)]
        if not days:
            continue

        end_year = year + 1 if len(months) > 1 and months[-1] < months[0] else year
        try:
            end = date(end_year, months[-1], days[-1])
            start = date(year, months[0], days[0])
        except ValueError:
            continue

        out.append({
            "date": end.isoformat(),
            "start": start.isoformat(),
            "scheduled": scheduled,
            "sep": False,     # not marked on historical pages; filled in dataset.py
            "days": max((end - start).days + 1, 1),
        })
    return out


def refresh(first_year: int = 1990, last_year: int | None = None,
            verbose: bool = True) -> list[dict]:
    """Rebuild the calendar from the Board's pages and cache it."""
    last_year = last_year or date.today().year
    meetings: dict[str, dict] = {}

    # The rolling page carries recent years *and* every future scheduled date.
    for rec in _parse_page(_fetch(CAL_URL)):
        meetings[rec["date"]] = rec
    if verbose:
        print("  fomccalendars.htm -> %d meetings" % len(meetings))

    # Historical year pages fill everything older.
    got_years = {r["date"][:4] for r in meetings.values()}
    for year in range(first_year, last_year + 1):
        if str(year) in got_years:
            continue
        try:
            page = _fetch(HIST_URL % year)
            recs = _parse_page(page) or _parse_historical(page)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print("  %d: skipped (%s)" % (year, e))
            continue
        for rec in recs:
            meetings.setdefault(rec["date"], rec)
        if verbose:
            print("  fomchistorical%d -> %d" % (year, len(recs)))
        time.sleep(0.25)   # be a polite scraper

    rows = sorted(meetings.values(), key=lambda r: r["date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"fetched": datetime.now().isoformat(timespec="seconds"),
         "source": CAL_URL, "meetings": rows}, indent=1), encoding="utf-8")
    return rows


def load(auto: bool = True) -> list[dict]:
    """Cached calendar; fetches once if missing."""
    if not OUT.exists():
        if not auto:
            raise SystemExit("no calendar cache -- run: python cli.py fetch")
        return refresh()
    return json.loads(OUT.read_text(encoding="utf-8"))["meetings"]


def next_meeting(after: str | None = None) -> dict | None:
    """The next scheduled meeting strictly after `after` (default: today)."""
    after = after or date.today().isoformat()
    for m in load():
        if m["date"] > after:
            return m
    return None


def upcoming(n: int = 8, after: str | None = None) -> list[dict]:
    after = after or date.today().isoformat()
    return [m for m in load() if m["date"] > after][:n]


if __name__ == "__main__":
    rows = refresh() if "--refresh" in sys.argv else load()
    print("%d meetings, %s -> %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    for m in upcoming(6):
        print("  next:", m["date"], "SEP" if m["sep"] else "")
