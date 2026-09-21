"""The dot plot, parsed from the Board's own projection table, point-in-time.

**The SEP prints at the same instant as the decision it accompanies.** The
Board's projections FAQ: the charts and tables "are released shortly after the
conclusion of the meeting". So the same strict gate applies as to statements --
a meeting may see the *previous* SEP and never its own. Every lookup here is
`published < when`, and `test_text.py` asserts it.

## What is actually retrievable

The dot plot does not go back to 1990, and it does not go back to the start of
the SEP either. Three facts, each measured rather than assumed:

  * The SEP itself begins **October 2007**, quarterly.
  * The 2007-2011 SEPs exist only as `FOMC{date}SEPcompilation.pdf` -- a scanned
    PDF -- and, more to the point, **they contain no interest-rate projections
    at all**. There is no dot plot in them to parse. The Board says so directly:
    "Since January 2012, the economic projections have also included information
    about policymakers' projections of the appropriate level of the target
    federal funds rate."
  * The machine-readable table, `fomcprojtabl{YYYYMMDD}.htm`, therefore starts
    at **2012-01-25** and runs to today. `index.py` establishes that by probing
    every meeting date back to 2007 and recording the 404s, not by trusting the
    sentence above.

That leaves **57 SEPs from 2012-01-25**, against a backtest that starts in 1997.
See `README.md` for what that does to coverage; the short version is that a dot
feature is null for the first 60% of the sample and no amount of care changes it.

## What is parsed

The dot *distribution*, not a summary line: the Board publishes a table of
"Midpoint of target range or target level (Percent)" against "Number of
participants" per horizon year. That is the individual dots in count form, so
the median and the dispersion are computed here from the participants rather
than read off a median row -- which means dispersion is available at all, and
the median is checkable against the Board's own.
"""

from __future__ import annotations

import bisect
import math
import re
from typing import NamedTuple

import boardsite as bs
import index as ix

# The header cell that identifies the dot table. Two wordings across the era:
#   2012-2015  "Target Federal Funds Rate at Year-End (Percent)"
#   2016-      "Midpoint of target range or target level (Percent)"
DOT_HEADER = re.compile(
    r"(?:midpoint of target range|target federal funds rate)", re.I)
YEAR = re.compile(r"^(19|20)\d{2}$")
NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")


class SEP(NamedTuple):
    date: str                    # the meeting it belongs to
    published: str               # when it printed == the meeting date
    url: str
    dots: dict                   # horizon label -> [rate, ...] one entry per participant


def _cells(row: str) -> list[str]:
    out = []
    for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S | re.I):
        t = re.sub(r"<[^>]+>", " ", c)
        t = t.replace("&nbsp;", " ").replace("\xa0", " ")
        out.append(re.sub(r"\s+", " ", t).strip())
    return out


def parse_dots(html: str) -> dict:
    """horizon -> the list of individual participant dots, expanded from counts.

    Horizon keys are the Board's own column labels: "2026", "2027", ...,
    "Longer run". Nothing is renamed, so a change in the Board's horizons shows
    up as a new key rather than being silently folded into an existing one.
    """
    # Rows, not tables. The Board's accessible-version pages nest tables and
    # leave some unclosed, so slicing on <table>...</table> put the dot header
    # in one fragment and its data rows in another -- which silently lost every
    # SEP from 2021 on while the pre-2021 ones parsed fine. Walking <tr> in
    # document order does not care how the tables are nested.
    rows = [_cells(r) for r in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html)]
    for k, head in enumerate(rows):
        if not head or not DOT_HEADER.search(head[0]):
            continue
        labels = [h for h in head[1:] if h]
        if not labels:
            continue
        dots: dict[str, list[float]] = {lab: [] for lab in labels}
        started = False
        for c in rows[k + 1:]:
            if not c or not NUMERIC.match(c[0]):
                if started:
                    break            # past the end of the dot block
                continue
            started = True
            rate = float(c[0])
            for i, lab in enumerate(labels):
                cell = c[i + 1] if i + 1 < len(c) else ""
                if NUMERIC.match(cell):
                    dots[lab].extend([rate] * int(float(cell)))
        if any(dots.values()):
            return {lab: v for lab, v in dots.items() if v}
    return {}


# -- corpus -----------------------------------------------------------------

_CACHE: list[SEP] | None = None


def load(*, refresh: bool = False, verbose: bool = False) -> list[SEP]:
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    docs = ix.load()
    out: list[SEP] = []
    for d in sorted(docs):
        url = docs[d].get("sep")
        if not url:
            continue
        html = bs.fetch(url, refresh=refresh, allow_404=True)
        if not html:
            continue
        dots = parse_dots(html)
        if not dots:
            # An SEP whose dot table did not parse is reported, never imputed.
            if verbose:
                print("  %s: no dot table parsed from %s" % (d, url))
            continue
        out.append(SEP(d, docs[d].get("sep_published", d), url, dots))
    out.sort(key=lambda s: s.published)
    _CACHE = out
    return out


# -- statistics over the dots -----------------------------------------------


def median(vals: list[float]) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def dispersion(vals: list[float]) -> float | None:
    """Population standard deviation of the dots, in percentage points.

    Chosen over the Board's published range because the range is two
    participants wide by construction and moves when one person moves; the sd
    is what actually tracks how split the Committee is.
    """
    if len(vals) < 2:
        return None
    mu = sum(vals) / len(vals)
    return math.sqrt(sum((x - mu) ** 2 for x in vals) / len(vals))


def horizon(s: SEP, year: int) -> list[float]:
    return s.dots.get(str(year), [])


# -- the point-in-time gate -------------------------------------------------


def before(when: str, *, corpus: list[SEP] | None = None) -> list[SEP]:
    """Every SEP published STRICTLY before `when`. Strict, for the same reason
    as `statements.before` -- an SEP is released with the decision."""
    c = corpus if corpus is not None else load()
    keys = [s.published for s in c]
    return c[:bisect.bisect_left(keys, when)]


def latest_before(when: str, *, corpus: list[SEP] | None = None) -> SEP | None:
    vis = before(when, corpus=corpus)
    return vis[-1] if vis else None


def prior(when: str, n: int = 1, *, corpus: list[SEP] | None = None) -> SEP | None:
    vis = before(when, corpus=corpus)
    return vis[-n] if len(vis) >= n else None


if __name__ == "__main__":
    import sys
    c = load(refresh="--refresh" in sys.argv, verbose=True)
    print("\n%d SEPs with a parseable dot table, %s -> %s"
          % (len(c), c[0].published, c[-1].published))
    for s in (c[0], c[len(c) // 2], c[-1]):
        ys = [k for k in s.dots if YEAR.match(k)]
        print("  %s  horizons=%s  n=%d  median(%s)=%.3f sd=%.3f"
              % (s.published, sorted(s.dots), len(s.dots[ys[0]]), ys[0],
                 median(s.dots[ys[0]]), dispersion(s.dots[ys[0]])))
