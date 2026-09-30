"""News sentiment and news-based uncertainty, point-in-time.

Two free daily series, both built by counting newspaper text, neither needing a
model call:

  SF Fed Daily News Sentiment Index   Shapiro, Sudhof & Wilson (FRBSF WP 2017-01),
                                      1980-01-01 -> ~3 days ago, updated weekly.
                                      Lexicon sentiment over economics articles in
                                      24 major US newspapers, as day fixed effects.
  Economic Policy Uncertainty (daily) Baker, Bloom & Davis, FRED `USEPUINDXD`,
                                      1985 -> yesterday. Share of articles with
                                      economy + policy + uncertainty terms.

WHY THESE AND NOT A HEADLINE FEED. A headline API cannot be backtested honestly:
nothing free archives what the wire said on a date in 1998, and a sentiment model
scored today over old headlines is scored with today's vocabulary. Both series
here are published by their authors as dated daily observations going back
decades, so every meeting in the backtest can be given exactly the window it
would have had.

THE LEAK THAT REMAINS, stated rather than hidden. The SF Fed index is re-estimated
on each weekly update (day fixed effects from a pooled regression), and its
newspaper set was switched to Factiva in April 2021 with the history recomputed.
So the historical values are today's vintage of the index, not what a reader saw
in 2008. The effect is second-order -- the fixed effects of a 1998 day barely move
when a 2026 week is added -- but it is not zero, and it is the same class of
compromise as the non-vintage market series in features.SERIES.

What IS enforced: publication lag. The index is published weekly with a ~3-day
lag, so every observation is stamped public `PUB_LAG_DAYS` after the day it
describes. A meeting can never see sentiment from the week it opens in.

Class C. No model, no key beyond FRED, no new dependency: the .xlsx is a zip of
XML and is read with the standard library.
"""

from __future__ import annotations

import html
import io
import json
import re
import time
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import fred

HERE = Path(__file__).resolve().parent
CACHE = HERE / "data" / "cache" / "news_sentiment.json"
SF_URL = "https://www.frbsf.org/wp-content/uploads/news_sentiment_data.xlsx"
TTL_S = 20 * 3600

# Weekly release, last observation ~3 days old at release -> worst case a
# day's value becomes public 10 days later. Using the worst case for every day
# costs a few days of freshness and buys a guarantee.
PUB_LAG_DAYS = 10

EPU_ID = "USEPUINDXD"

_EXCEL_EPOCH = date(1899, 12, 30)


def _excel_date(serial: float) -> str:
    return (_EXCEL_EPOCH + timedelta(days=int(serial))).isoformat()


def _parse_xlsx(raw: bytes) -> list[tuple[str, float]]:
    """The 'Data' sheet -> [(iso date, sentiment)], oldest first.

    Located by name through workbook.xml + its rels rather than assumed to be
    sheet2, because the Board's sister bank has reordered sheets before.
    """
    z = zipfile.ZipFile(io.BytesIO(raw))
    wb = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    m = re.search(r'<sheet [^>]*name="Data"[^>]*r:id="([^"]+)"', wb)
    if not m:
        raise ValueError("news sentiment workbook has no 'Data' sheet")
    t = re.search(r'<Relationship [^>]*Id="%s"[^>]*Target="([^"]+)"' % m.group(1), rels) \
        or re.search(r'<Relationship [^>]*Target="([^"]+)"[^>]*Id="%s"' % m.group(1), rels)
    if not t:
        raise ValueError("news sentiment workbook: cannot resolve Data sheet")
    target = t.group(1).lstrip("/")
    sheet = z.read(target if target.startswith("xl/") else "xl/" + target).decode("utf-8")

    out = []
    for row in re.findall(r"<row [^>]*>(.*?)</row>", sheet, re.S):
        cells = dict(re.findall(r'<c r="([A-Z]+)\d+"(?![^>]*t="s")[^>]*>(?:<f>.*?</f>)?<v>([^<]*)</v>', row))
        a, b = cells.get("A"), cells.get("B")
        if a is None or b is None:
            continue          # the header row is shared strings; skipped here
        try:
            out.append((_excel_date(float(a)), float(b)))
        except ValueError:
            continue
    out.sort()
    if len(out) < 1000:
        raise ValueError("news sentiment parse produced only %d rows" % len(out))
    return out


def _download() -> bytes:
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(SF_URL, headers={
                "User-Agent": "Mozilla/5.0 (fomc-engine; research; +github.com/kdjzckl-sys/fomc-engine)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - network; retry on anything
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("news sentiment download failed: %s" % last)


def sentiment(refresh: bool = False) -> fred.Panel:
    """SF Fed daily news sentiment as a point-in-time Panel."""
    rows = None
    if CACHE.exists() and not refresh:
        blob = json.loads(CACHE.read_text(encoding="utf-8"))
        if time.time() - blob.get("fetched", 0) < TTL_S:
            rows = blob["rows"]
    if rows is None:
        try:
            rows = _parse_xlsx(_download())
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({"fetched": time.time(), "source": SF_URL,
                                         "rows": rows}), encoding="utf-8")
        except Exception:
            # A stale cache beats no column: the lag stamp still holds, and
            # provenance() reports how old it is.
            if not CACHE.exists():
                raise
            rows = json.loads(CACHE.read_text(encoding="utf-8"))["rows"]
    return fred.Panel([fred.Obs(d, v, fred.shift_days(d, PUB_LAG_DAYS)) for d, v in rows])


def epu(refresh: bool = False) -> fred.Panel:
    """Daily Economic Policy Uncertainty, publication-lagged one day.

    FRED serves it as a current-vintage series with pub == date; the index for a
    day is computed from that day's papers and posted the next, so +1.
    """
    obs = fred.series(EPU_ID, vintage=False, refresh=refresh)
    return fred.Panel([fred.Obs(o.date, o.value, fred.shift_days(o.date, 1)) for o in obs])


def provenance() -> dict:
    out = {"source": SF_URL, "pub_lag_days": PUB_LAG_DAYS}
    if CACHE.exists():
        blob = json.loads(CACHE.read_text(encoding="utf-8"))
        rows = blob.get("rows") or []
        out.update(first=rows[0][0] if rows else None, last=rows[-1][0] if rows else None,
                   n=len(rows), fetched_age_h=round((time.time() - blob.get("fetched", 0)) / 3600, 1))
    return out


if __name__ == "__main__":
    p = sentiment(refresh=True)
    print("news sentiment: %d obs %s -> %s (last public %s)" % (len(p), p[0].date, p[-1].date, p[-1].pub))
    e = epu()
    print("EPU: %d obs %s -> %s" % (len(e), e[0].date, e[-1].date))
