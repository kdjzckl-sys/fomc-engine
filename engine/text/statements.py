"""FOMC statement text, cached, with a point-in-time gate.

The gate is the only interesting thing in this file, so it goes first.

**A meeting's own statement is published at that meeting.** It comes out at
2:00 p.m. on the afternoon the meeting ends, in the same release as the
decision. Feeding meeting *i*'s statement into a forecast of meeting *i* is not
a subtle leak, it is the answer: the statement literally contains the sentence
"the Committee decided to raise the target range". A model handed that would
score near-perfect and be worth nothing.

So every lookup here is **strictly before**, not `<=`:

    fred.as_of(obs, when)          ->  pub <= when     (a CPI print that morning
                                                        IS on the Committee's desk)
    statements.latest_before(when) ->  pub <  when     (the 2 p.m. statement is NOT)

That one character is the difference between a working feature and a fraud, and
`test_text.py` asserts it on every meeting in the record rather than trusting
this paragraph. The rule generalises: the most recent statement available to a
meeting is the *previous* meeting's, which is what `prior()` returns.

Storage: one JSON per document under data/cache/text/, fetched once. The Board's
archive is frozen, so nothing here has a TTL.
"""

from __future__ import annotations

import bisect
import re
from typing import NamedTuple

import boardsite as bs
import index as ix
import repo


class Statement(NamedTuple):
    date: str           # the meeting (or intermeeting action) the statement belongs to
    published: str      # when it printed. For a statement this equals `date`.
    url: str
    text: str           # cleaned body, statement language only
    kind: str           # "meeting" if the date is on the Board's calendar, else "other"


# -- body extraction --------------------------------------------------------
#
# Four page layouts across 32 years. The extractor has one job: return the same
# token stream for the same statement regardless of which layout served it,
# because the length and diff features would otherwise measure the Board's web
# redesigns instead of the Committee's language.

_ARTICLE = re.compile(r'<div[^>]+id="article"', re.I)
_SHARE = re.compile(r'(?is)<ul class="list-unstyled">.*?</ul>\s*</div>')
_LAST_UPDATE = re.compile(r'(?i)<div[^>]+id="lastUpdate"')

# Everything above one of these is masthead; the statement starts after it.
_HEAD_CUTS = (
    "for release at",              # 2006-
    "for immediate release",       # 1994-2005
)
# Everything below one of these is boilerplate, navigation or a sibling release.
_TAIL_CUTS = (
    "for media inquiries",
    "implementation note issued",
    "last update",
    "accessibility",
    "monetary policy\nhome",
    "board of governors of the federal reserve system\n",
)
# Sibling releases the Board links at the foot of a statement. They are other
# documents, not this statement's language, and leaving them in added up to 100
# words to every 2013-2014 row -- which would have shown up as a fake surge in
# the length feature exactly through the taper.
_TAIL_RE = re.compile(
    r"(?im)^\s*(?:\d{4} Monetary policy|Home \| News|Home\s*\|\s*News and events|"
    r"Return to top|Share|Skip to main content|Statement Regarding\b|"
    r"Statement on Longer-Run Goals\b|Implementation Note\b|Related Content\b)")


def _body(html: str) -> str:
    """The statement language, with masthead and chrome removed."""
    seg = html
    m = _ARTICLE.search(html)
    if m:                                        # 2006- : the release is one div
        end = _LAST_UPDATE.search(html, m.end())
        seg = html[m.start():end.start() if end else len(html)]
        seg = _SHARE.sub(" ", seg)

    text = bs.to_text(seg)
    low = text.lower()

    cut = -1
    for marker in _HEAD_CUTS:
        i = low.find(marker)
        if i >= 0:
            j = text.find("\n", i)
            cut = max(cut, j if j >= 0 else i + len(marker))
    if cut >= 0:
        text = text[cut:]

    low = text.lower()
    end = len(text)
    for marker in _TAIL_CUTS:
        i = low.find(marker)
        if i >= 0:
            end = min(end, i)
    m = _TAIL_RE.search(text)
    if m:
        end = min(end, m.start())
    text = text[:end]

    return re.sub(r"\n{3,}", "\n\n", text).strip()


# -- corpus -----------------------------------------------------------------

_CACHE: list[Statement] | None = None


def load(*, refresh: bool = False, verbose: bool = False) -> list[Statement]:
    """Every statement the Board publishes, oldest first.

    Sorted by publication date, which for a statement is the meeting date --
    that identity is asserted in the tests rather than assumed here.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE

    docs = ix.load()
    # Not every document the Board files under "Statement" is a policy
    # statement: the August 2007 liquidity release and the March 2020 facility
    # announcements are in the same bucket. They are real, they are genuinely
    # published before the next meeting, and they are a quarter the length of a
    # policy statement -- so a length feature that mixed them in would read a
    # liquidity notice as the Committee suddenly going quiet. They are kept and
    # tagged rather than dropped, and `prior()` filters to meetings by default.
    cal = repo.meeting_dates()
    out: list[Statement] = []
    for d in sorted(docs):
        rec = docs[d]
        url = rec.get("statement")
        if not url:
            continue
        html = bs.fetch(url, refresh=refresh, allow_404=True)
        if not html:
            if verbose:
                print("  %s: statement link is dead (%s)" % (d, url))
            continue
        text = _body(html)
        if len(text) < 200:
            # A statement is never this short. Rather than ship a truncated one
            # into a length feature, drop it and say so.
            if verbose:
                print("  %s: body too short (%d chars), dropped" % (d, len(text)))
            continue
        out.append(Statement(d, rec.get("statement_published", d), url, text,
                             "meeting" if d in cal else "other"))

    out.sort(key=lambda s: (s.published, s.date))
    _CACHE = out
    return out


# -- the point-in-time gate -------------------------------------------------


def before(when: str, *, meetings_only: bool = True,
           corpus: list[Statement] | None = None) -> list[Statement]:
    """Every statement published STRICTLY before `when`, oldest first.

    Strict, not `<=`. See the module docstring: a statement printed on `when` is
    printed *with* that meeting's decision, so it is not information the
    forecast is allowed to have.
    """
    c = corpus if corpus is not None else load()
    keys = [s.published for s in c]
    vis = c[:bisect.bisect_left(keys, when)]
    return [s for s in vis if s.kind == "meeting"] if meetings_only else vis


def latest_before(when: str, *, meetings_only: bool = True,
                  corpus: list[Statement] | None = None) -> Statement | None:
    """The most recent statement published strictly before `when`."""
    vis = before(when, meetings_only=meetings_only, corpus=corpus)
    return vis[-1] if vis else None


def prior(when: str, n: int = 1, *, meetings_only: bool = True,
          corpus: list[Statement] | None = None) -> Statement | None:
    """The n-th most recent statement strictly before `when` (n=1 is the latest)."""
    vis = before(when, meetings_only=meetings_only, corpus=corpus)
    return vis[-n] if len(vis) >= n else None


def coverage(dates: list[str]) -> dict:
    """How many of `dates` have a prior statement, and how stale it is.

    The honest answer to "can this feature be computed for the backtest", per
    meeting, rather than a claim about the archive as a whole. `two_prior` is
    the one that limits the diff feature -- a redline needs two statements, not
    one, so it always covers fewer meetings than the level does.
    """
    c = load()
    have = two = 0
    ages = []
    for d in dates:
        s = latest_before(d, corpus=c)
        if not s:
            continue
        have += 1
        ages.append(_days(s.published, d))
        if prior(d, 2, corpus=c):
            two += 1
    ages.sort()
    return {"n": len(dates), "with_prior_statement": have, "two_prior": two,
            "median_age_days": ages[len(ages) // 2] if ages else None,
            "max_age_days": ages[-1] if ages else None}


def _days(a: str, b: str) -> int:
    from datetime import date
    pa = date(int(a[:4]), int(a[5:7]), int(a[8:10]))
    pb = date(int(b[:4]), int(b[5:7]), int(b[8:10]))
    return (pb - pa).days


if __name__ == "__main__":
    import sys
    c = load(refresh="--refresh" in sys.argv, verbose=True)
    lens = sorted(len(bs.words(s.text)) for s in c)
    print("\n%d statements, %s -> %s" % (len(c), c[0].published, c[-1].published))
    print("words: min %d  p25 %d  median %d  p75 %d  max %d"
          % (lens[0], lens[len(lens) // 4], lens[len(lens) // 2],
             lens[3 * len(lens) // 4], lens[-1]))
    for s in (c[0], c[len(c) // 2], c[-1]):
        print("\n--- %s (%d words) %s" % (s.published, len(bs.words(s.text)), s.url))
        print(s.text[:400].replace("\n", " | "))
