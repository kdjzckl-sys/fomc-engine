"""Invariants for the statement / SEP data layer. No network: runs off the cache.

The first block is the only one that really matters. Everything else in this
package is a convenience; the leak gate is the thing that makes it usable at
all, so it is asserted on every meeting in the record rather than spot-checked.

    python test_text.py          (or: python cli.py test)
"""

from __future__ import annotations

import json
import re

import boardsite as bs
import derive
import index as ix
import lexicon as lx
import report
import repo
import sep as sep_mod
import statements as st

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def main() -> int:
    corpus = st.load()
    seps = sep_mod.load()
    docs = ix.load()
    meetings = repo.backtest_dates()

    # ------------------------------------------------------------------ leaks
    print("\nthe point-in-time gate  (the only block that matters)")

    # A statement is published AT the meeting it belongs to. If that identity
    # ever breaks, every strict comparison below is comparing the wrong dates.
    check("statement publication date == its meeting date",
          all(s.published == s.date for s in corpus),
          [s.date for s in corpus if s.published != s.date][:3])
    check("SEP publication date == its meeting date",
          all(s.published == s.date for s in seps),
          [s.date for s in seps if s.published != s.date][:3])

    bad = [(d, s.published) for d in meetings
           for s in [st.latest_before(d, corpus=corpus)] if s and s.published >= d]
    check("no statement visible to a meeting on or before its publication date",
          not bad, bad[:3])

    bad = [(d, s.published) for d in meetings
           for s in [sep_mod.latest_before(d, corpus=seps)] if s and s.published >= d]
    check("no SEP visible to a meeting on or before its publication date",
          not bad, bad[:3])

    # The same assertion one level up: the assembled feature row must not carry
    # a source date that reaches the meeting. This is what would actually break
    # if someone later relaxed a comparison inside derive.row().
    rows = derive.build(meetings)
    bad = [(r["date"], r.get("stmt_date"), r.get("sep_date")) for r in rows
           if (r.get("stmt_date") and r["stmt_date"] >= r["date"])
           or (r.get("sep_date") and r["sep_date"] >= r["date"])]
    check("no derived row sources a document dated on or after its meeting",
          not bad, bad[:3])

    # An exhaustive version over every date the corpus knows, not just the 233.
    alldates = sorted({s.date for s in corpus} | {s.date for s in seps})
    bad = [d for d in alldates
           if any(s.published >= d for s in st.before(d, meetings_only=False,
                                                      corpus=corpus)[-1:])
           or any(s.published >= d for s in sep_mod.before(d, corpus=seps)[-1:])]
    check("before() is strict on every document date in the record",
          not bad, bad[:3])

    # Minutes are the one document that is genuinely new information between
    # meetings, and only because they print late. If a release date were ever
    # stamped as the meeting date, they would become a leak too.
    mins = [(d, r["minutes_published"]) for d, r in docs.items()
            if r.get("minutes_published")]
    check("every minutes release date is strictly after its meeting",
          all(p > d for d, p in mins),
          [(d, p) for d, p in mins if p <= d][:3])
    lags = sorted((p[:4] and _days(d, p)) for d, p in mins)
    check("minutes lag is 2-8 weeks (median ~3)",
          14 <= lags[len(lags) // 2] <= 56, "median %d days" % lags[len(lags) // 2])

    # ------------------------------------------------------------------ cache
    print("\ncache")
    url = "https://www.federalreserve.gov/__test__/roundtrip.htm"
    body = "<html><p>Round trip &amp; nothing else.</p></html>"
    slot = bs._slot(url)
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text(json.dumps({"url": url, "fetched": "test", "body": body}),
                    encoding="utf-8")
    try:
        check("cache round-trips a document byte for byte",
              bs.fetch(url) == body)
        check("cache key is stable across calls", bs._slot(url) == slot)
    finally:
        slot.unlink(missing_ok=True)

    check("every statement in the corpus has a cache entry",
          all(bs._slot(s.url).exists() for s in corpus))
    check("index cache reloads to the same document count",
          len(ix.load()) == len(docs), "%d vs %d" % (len(ix.load()), len(docs)))

    # ------------------------------------------------------- text extraction
    print("\ntext extraction")
    chrome = re.compile(r"(?i)skip to main content|share\s*$|last update:|"
                        r"board of governors of the federal reserve system\s*$|"
                        r"privacy program|accessibility")
    dirty = [s.date for s in corpus if chrome.search(s.text)]
    check("no statement body carries page chrome", not dirty, dirty[:5])
    check("every statement body is non-trivial",
          all(len(bs.words(s.text)) >= 30 for s in corpus),
          [(s.date, len(bs.words(s.text))) for s in corpus
           if len(bs.words(s.text)) < 30][:3])
    # The four page layouts must produce comparable token streams. A 2026
    # statement is not 10x a 2005 one; if the extractor regressed on one era,
    # that era's median length would move by an order of magnitude.
    med = {}
    for era, lo, hi in report.ERAS:
        w = sorted(len(bs.words(s.text)) for s in corpus if lo <= s.date < hi)
        med[era] = w[len(w) // 2] if w else 0
    check("statement length is era-comparable, not layout-driven",
          all(60 <= v <= 900 for v in med.values()), med)

    # ------------------------------------------------------------- arithmetic
    print("\nlexicon arithmetic (fixtures)")
    for text, hawk, dove, why in [
        ("inflation has moderated", 0, 1, "down + price"),
        ("the unemployment rate has risen", 0, 1, "up + slack inverts"),
        ("inflation is not expected to rise", 0, 1, "negation flips"),
        ("the Committee decided to raise the target range", 1, 0, "up + policy"),
        ("the pace of economic growth has slowed", 0, 1, "down + activity"),
    ]:
        s = lx.score(bs.words(text))
        check("lexicon: %-34s -> %s" % ('"' + text + '"', why),
              (s["hawk"], s["dove"]) == (hawk, dove),
              "got hawk=%d dove=%d" % (s["hawk"], s["dove"]))

    print("\ndiff arithmetic (fixture)")
    old = "the committee decided to maintain the target range"
    new = "the committee decided to lower the target range today"
    r = derive.redline(new, old)
    check("redline counts one replacement and one insertion",
          (r["added"], r["removed"]) == (2, 1), r)
    check("redline changed_frac is (added+removed)/(len_a+len_b)",
          abs(r["changed_frac"] - 3 / 17) < 1e-4, r["changed_frac"])
    check("redline of a statement against itself is zero",
          derive.redline(corpus[-1].text, corpus[-1].text)["changed_frac"] == 0.0)

    print("\nbalance-of-risks classification (fixtures)")
    for text, want in [
        ("the Committee believes the risks are weighted mainly toward conditions "
         "that may generate heightened inflation pressures.", 1),
        ("the Committee believes the risks are weighted mainly toward conditions "
         "that may generate economic weakness.", -1),
        ("The Committee perceives that the upside and downside risks to the "
         "attainment of sustainable growth are roughly equal.", 0),
        ("Moreover, there are significant downside risks to the economic outlook.", -1),
        ("The Committee sees the risks to the outlook for economic activity and "
         "the labor market as nearly balanced.", 0),
        ("Economic activity is expanding at a solid pace.", None),
    ]:
        got = derive.risk_tilt(text)[0]
        check("risk tilt %+2s: %s" % (got, text[:52] + "..."), got == want, got)

    print("\ndissent parsing (fixtures)")
    d = derive.dissents(
        "Voting against this action were Stephen I. Miran, who preferred to lower "
        "the target range for the federal funds rate by 1/2 percentage point at "
        "this meeting, and Jeffrey R. Schmid, who preferred no change to the "
        "target range at this meeting.", -25.0)
    check("two dissenters, one dovish and one hawkish-by-context",
          (d["n"], d["hawk"], d["dove"]) == (2, 1, 1), d)
    d = derive.dissents("Voting against the action: none.", 0.0)
    check("an explicit 'none' is zero, not null", d["n"] == 0, d)
    d = derive.dissents("The Committee decided to raise the target range.", 25.0)
    check("a statement with no roll call is null, not zero", d["n"] is None, d)
    d = derive.dissents(
        "Voting against the action were Beth M. Hammack, Neel Kashkari, and "
        "Lorie K. Logan, who preferred to raise the target range by 1/4 "
        "percentage point at this meeting.", 0.0)
    check("a shared clause labels all three dissenters",
          (d["n"], d["hawk"]) == (3, 3), d)

    # ------------------------------------------------------------------- SEP
    print("\nSEP dots")
    check("every SEP parsed at least one horizon", all(s.dots for s in seps))
    check("participant counts are plausible (7-25)",
          all(7 <= len(v) <= 25 for s in seps for v in s.dots.values()),
          [(s.date, {k: len(v) for k, v in s.dots.items()}) for s in seps
           if any(not 7 <= len(v) <= 25 for v in s.dots.values())][:2])
    check("the dot record starts at the first dot plot (January 2012)",
          seps[0].published == "2012-01-25", seps[0].published)

    # The computed median has to agree with the Board's own published median,
    # or the dot parser is reading a different table from the one it claims.
    agree = disagree = absent = 0
    worst = []
    for s in seps:
        html = bs.fetch(s.url, allow_404=True)
        pub = _published_median(html, list(s.dots))
        if pub is None:
            absent += 1
            continue
        for lab, want in zip(s.dots, pub):
            got = sep_mod.median(s.dots[lab])
            if got is None:
                continue
            if abs(got - want) <= 0.051:      # one rounding step at 1 decimal
                agree += 1
            else:
                disagree += 1
                worst.append((s.date, lab, got, want))
    check("computed dot medians match the Board's published medians",
          disagree == 0, "%d disagree of %d: %s" % (disagree, agree + disagree, worst[:3]))
    print("        (%d medians checked, %d SEPs publish no median row)" % (agree, absent))

    # ------------------------------------------------------------- coverage
    print("\ncoverage is reported, never imputed")
    cov = report.coverage(meetings)
    check("SEP columns are null before 2012, not zero-filled",
          cov["columns"]["sep_dot_y0"]["1997-2007"] == "0/84",
          cov["columns"]["sep_dot_y0"])
    check("dissent columns are null before the roll call entered the statement",
          cov["columns"]["stmt_dissents"]["n"] < len(meetings),
          cov["columns"]["stmt_dissents"])
    check("the derived row set is exactly the backtest window",
          len(rows) == 233 and rows[0]["date"] == "1997-08-19"
          and rows[-1]["date"] == "2026-09-16",
          (len(rows), rows[0]["date"], rows[-1]["date"]))

    print("\n" + ("ALL PASS" if not FAIL else
                  "%d FAILED: %s" % (len(FAIL), ", ".join(FAIL))))
    return 1 if FAIL else 0


_NUM = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)$")


def _published_median(html: str, labels: list[str]) -> list[float] | None:
    """The Board's own 'Federal funds rate' median row from a projection table."""
    if not html:
        return None
    for cells in (sep_mod._cells(r)
                  for r in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html)):
        if cells and re.match(r"^federal funds rate\s*\d*$", cells[0].strip(), re.I):
            nums = [c for c in cells[1:] if _NUM.match(c)]
            if len(nums) >= len(labels):
                return [float(x) for x in nums[:len(labels)]]
    return None


def _days(a: str, b: str) -> int:
    from datetime import date
    return (date(int(b[:4]), int(b[5:7]), int(b[8:10]))
            - date(int(a[:4]), int(a[5:7]), int(a[8:10]))).days


if __name__ == "__main__":
    raise SystemExit(main())
