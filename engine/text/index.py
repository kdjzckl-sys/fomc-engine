"""The document index: which statement / SEP / minutes exists, and when it printed.

Scraped, not hand-typed, for the same reason `calendar_fed.py` scrapes the
meeting dates: a typed table of 300 URLs across four site redesigns is a
fabrication risk that rots every January.

Two index pages carry every link:

  * `fomccalendars.htm`          -- roughly the last five years plus the future
  * `fomchistorical{YYYY}.htm`   -- one page per year, back to 1936

They do not share a layout, and the documents they point at sit in **four**
different URL schemes depending on era. Rather than guess a scheme per year,
this module harvests whatever the Board links, and takes the document's date
from the eight digits the Board itself put in the URL:

    1994-1998   /fomc/{YYYYMMDD}DEFAULT.htm
    1999-2005   /boarddocs/press/{general,monetary}/{YYYY}/{YYYYMMDD}/[default.htm]
    2006-2015   /newsevents/press/monetary/{YYYYMMDD}a.htm
    2016-       /newsevents/pressreleases/monetary{YYYYMMDD}a.htm

**Publication dates are the whole point of this file.** Three documents attach
to one meeting and they do not print at the same moment:

  statement  published the afternoon the meeting ends -- SIMULTANEOUS with the
             decision, therefore never available to that meeting's own forecast.
  SEP        "released shortly after the conclusion of the meeting", per the
             Board's own projections FAQ. Same timestamp as the statement.
  minutes    released with a three-week lag, and the exact release date is
             printed on the index page as "(Released February 19, 2025)".
             That parenthetical is the only one of the three that is genuinely
             new information between meetings, so it is parsed rather than
             assumed to be "meeting + 21 days".

Cached to data/cache/text/index.json.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

import boardsite as bs

OUT = bs.CACHE / "index.json"

CAL_URL = bs.BASE + "/monetarypolicy/fomccalendars.htm"
HIST_URL = bs.BASE + "/monetarypolicy/fomchistorical%d.htm"

# The Board's historical pages stop five years short of today (the transcript
# embargo); the rolling calendar covers the rest. Overlap is harmless -- the
# index is a dict keyed by date, and the historical page never overwrites.
FIRST_YEAR = 1990

A = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
DATE8 = re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)")
RELEASED = re.compile(
    r"\(\s*Released\s+([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\s*\)", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

STATEMENT_TEXT = re.compile(r"^\s*statement\s*$", re.I)
MINUTES_URL = re.compile(r"(?:fomcminutes|/fomc/minutes/|/fomc/MINUTES/)", re.I)
PROJTABL_URL = re.compile(r"fomcprojtabl(\d{8})\.htm", re.I)


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).replace("\xa0", " ").strip()


def _date_in(href: str) -> str | None:
    m = DATE8.search(href)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _released(text: str) -> str | None:
    m = RELEASED.search(text)
    if not m:
        return None
    mon = MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    try:
        return date(int(m.group(3)), mon, int(m.group(2))).isoformat()
    except ValueError:
        return None


def _harvest(html: str, out: dict) -> None:
    """Pull statement / SEP / minutes links out of one index page.

    The modern calendar writes the statement as `Statement: PDF | HTML`; every
    historical page writes it as an anchor whose text is exactly "Statement".
    Both are handled, and an anchor is only accepted if the Board put a date in
    its href -- which is what keeps a nav link out of the index.
    """
    # Statement, modern layout: a "Statement:" label, then PDF | HTML.
    for m in re.finditer(r"Statement\s*:\s*</strong>(.{0,600}?)</div>", html, re.S | re.I):
        for href, txt in A.findall(m.group(1)):
            if _plain(txt).lower() != "html":
                continue
            d = _date_in(href)
            if d:
                out.setdefault(d, {}).setdefault("statement", bs.url_abs(href))

    # Statement, every historical layout: anchor text is exactly "Statement".
    for href, txt in A.findall(html):
        d = _date_in(href)
        if d and STATEMENT_TEXT.match(_plain(txt)):
            out.setdefault(d, {}).setdefault("statement", bs.url_abs(href))

    # SEP: only the HTML projection table is machine-readable. The pre-2012
    # SEPs exist as a PDF compilation only and carry no rate projections at
    # all, so there is nothing for this package to read in them.
    # Match inside the href, and keep the WHOLE href. Matching the bare
    # `fomcprojtabl########.htm` fragment and absolutising that dropped the
    # /monetarypolicy/ directory, which 404'd every SEP from 2021 on -- quietly,
    # because a missing document is a legitimate answer in this package.
    for href, _ in A.findall(html):
        if not PROJTABL_URL.search(href):
            continue
        d = _date_in(href)
        if d:
            out.setdefault(d, {}).setdefault("sep", bs.url_abs(href))

    # Minutes, with the release date the Board prints beside the link. Two
    # layouts, and the difference is which side of the anchor it lands on:
    #   2005: <a>Minutes</a> (Released Feb 23, 2005)
    #   2019: Minutes (Released February 20, 2019): <a>HTML</a> | <a>PDF</a>
    # Looking only forward found 163 of 268 and silently lost the whole
    # 2007-2020 block, so the window straddles the anchor.
    for m in A.finditer(html):
        href = m.group(1)
        if not MINUTES_URL.search(href) or href.lower().endswith(".pdf"):
            continue
        d = _date_in(href)
        if not d:
            continue
        rec = out.setdefault(d, {})
        rec.setdefault("minutes", bs.url_abs(href))
        window = _plain(html[max(0, m.start() - 220):m.end() + 300])
        rel = _released(window)
        if rel and not rec.get("minutes_published"):
            rec["minutes_published"] = rel


SEP_PROBE_FROM = "2007-01-01"   # the first SEP of any kind was October 2007


def _probe_sep(rows: dict, *, refresh_http: bool = False, verbose: bool = True) -> None:
    """Find the HTML projection tables the index pages do not link.

    The rolling calendar links `fomcprojtabl{date}.htm` for the years it covers;
    the historical year pages link only `FOMC{date}SEPcompilation.pdf`, which is
    a scan, not a table. So the HTML table for 2012-2020 exists but is not
    reachable by harvesting links, and the only honest way to find it is to ask
    the documented URL for every meeting date and record what answers.

    Every 404 is cached exactly like a 200, so this costs one pass, once, and
    the resulting "no SEP here" is a measured fact rather than an assumption
    about when the Board started publishing.
    """
    tried = hit = 0
    for d in sorted(rows):
        if d < SEP_PROBE_FROM or "statement" not in rows[d]:
            continue
        if "sep" in rows[d]:
            continue
        url = bs.BASE + "/monetarypolicy/fomcprojtabl%s.htm" % d.replace("-", "")
        tried += 1
        if bs.fetch(url, refresh=refresh_http, allow_404=True):
            rows[d]["sep"] = url
            hit += 1
    if verbose:
        print("  probed %d meeting dates for an HTML projection table -> %d found"
              % (tried, hit))


def refresh(*, refresh_http: bool = False, last_year: int | None = None,
            verbose: bool = True) -> dict:
    """Rebuild the index from the Board's pages and cache it."""
    last_year = last_year or date.today().year
    out: dict[str, dict] = {}

    html = bs.fetch(CAL_URL, refresh=refresh_http, ttl=bs.INDEX_TTL_S)
    _harvest(html or "", out)
    if verbose:
        print("  fomccalendars.htm -> %d dated documents" % len(out))

    for year in range(FIRST_YEAR, last_year + 1):
        page = bs.fetch(HIST_URL % year, refresh=refresh_http,
                        ttl=bs.INDEX_TTL_S, allow_404=True)
        if not page:
            if verbose:
                print("  fomchistorical%d -> not published" % year)
            continue
        before = len(out)
        _harvest(page, out)
        if verbose:
            print("  fomchistorical%d -> +%d" % (year, len(out) - before))

    _probe_sep(out, refresh_http=refresh_http, verbose=verbose)

    rows = {}
    for d in sorted(out):
        rec = dict(out[d])
        rec["date"] = d
        # A statement and an SEP print at the same instant as the decision they
        # describe. That is not an approximation, it is the Board's release
        # schedule, and it is the fact the whole point-in-time gate rests on.
        if "statement" in rec:
            rec["statement_published"] = d
        if "sep" in rec:
            rec["sep_published"] = d
        rows[d] = rec

    bs.CACHE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"built": datetime.now().isoformat(timespec="seconds"),
         "source": CAL_URL, "n": len(rows), "documents": rows}, indent=1),
        encoding="utf-8")
    return rows


def load(*, auto: bool = True, verbose: bool = False) -> dict:
    """Cached index; builds it once if missing."""
    if not OUT.exists():
        if not auto:
            raise SystemExit("no text index -- run: python cli.py index")
        return refresh(verbose=verbose)
    return json.loads(OUT.read_text(encoding="utf-8"))["documents"]


if __name__ == "__main__":
    import sys
    docs = refresh() if "--refresh" in sys.argv else load()
    st = sorted(d for d, r in docs.items() if "statement" in r)
    sep = sorted(d for d, r in docs.items() if "sep" in r)
    mi = sorted(d for d, r in docs.items() if "minutes" in r)
    print("%d dated documents" % len(docs))
    print("  statements %d  %s -> %s" % (len(st), st[0], st[-1]))
    print("  SEP        %d  %s -> %s" % (len(sep), sep[0], sep[-1]))
    print("  minutes    %d  %s -> %s" % (len(mi), mi[0], mi[-1]))
    dated = sum(1 for d in mi if docs[d].get("minutes_published"))
    print("  minutes with a parsed release date: %d/%d" % (dated, len(mi)))
