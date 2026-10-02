"""Fed funds futures: what the market had actually priced, back to 1989.

The README named this as the engine's single biggest weakness -- `DGS1 - EFFR`
and `DGS3MO - EFFR` standing in for the instrument the market really prices FOMC
decisions in. This module closes it, and is explicit about where the real data
stops and the old proxy takes over.

WHAT WAS FOUND (every candidate fetched on 2026-09-18, nothing assumed)

  USABLE

  1. **Nakamura-Steinsson replication archive**, Harvard Dataverse, **CC0**.
     `Replication_Online/Stata/Data_Orig/fedfutures.csv` inside
     dataverse.harvard.edu/api/access/datafile/3157930 (9,475,466 bytes).
     **1989-03-27 -> 2015-02-11**, 9,453 daily rows, the full **7-contract
     strip** in both Datastream roll conventions. FFE1C is the current-month
     contract; FFB(k) = FFE(k+1) is the contract k months ahead. Frozen since
     2018, which is fine -- so is 2015.

  2. **Yahoo Finance `ZQ=F`** (the front 30-day fed funds contract).
     query1.finance.yahoo.com/v8/finance/chart/ZQ%3DF with explicit
     `period1`/`period2` -- **2000-09-01 -> today, 6,523 closes**, no key, no
     cookie, no crumb. (`range=max` silently downsamples to 268 points; do not
     use it.) Undocumented internal endpoint, so it is used for one private
     research repo and nothing is redistributed.
     Per-contract symbols (`ZQZ26.CBT`) also resolve while a contract is listed,
     which is what gives the *live* forecast a real strip rather than just the
     front month. Expired contracts are purged, so they cannot rebuild history.

  3. **Atlanta Fed Market Probability Tracker**, `mpt_histdata.xlsx`.
     **2023-03-29 -> today**, daily, free, no key. Full option-implied
     probability distributions, so it carries something the futures strip cannot:
     the market's uncertainty, not just its mean. SOFR options, which is why it
     does not start earlier. Licence, quoted from its LICENSE sheet: "Use of this
     data is permitted for personal and educational purposes only."

  THE JOIN IS VALIDATED, NOT ASSUMED. Yahoo and the NS front contract overlap
  on **3,590 trading days (2000-09-01 -> 2015-02-11)**. Mean difference 0.15bp,
  sd 1.6bp, and 0.5% of days differ by more than 5bp. `test_fomc.py` re-checks
  this every run rather than trusting a number in a comment.

  NOT USABLE -- checked, not guessed

  * **CME Group** -- 403 on the settlement page, on the JSON API and on
    robots.txt, with the body naming automated access as prohibited. There is no
    free historical archive. Not scraped, not worked around.
  * **Stooq** -- serves a JavaScript proof-of-work page instead of CSV; its bulk
    download returns "Access denied". Not machine-readable.
  * **Nasdaq Data Link / Quandl `CHRIS/CME_FF1`**, **investing.com**,
    **Barchart** -- bot-walled and/or ToS-prohibited and/or key-gated.
  * **FRED / ALFRED** -- has no futures- or market-expectation-derived series at
    all. Searched; the hits are repo operations, the effective rate, the target
    range, and the SEP dot plot. The dot plot is the Committee's own projection,
    not the market's.
  * **Cleveland Fed "Fed Funds Rate Predictions"** -- 404, and absent from the
    live indicator index. Their "Simple Monetary Policy Rules" file does exist
    but is rule-based, not market-implied.
  * **High-frequency surprise datasets** (FRBSF USMPD, Bauer-Swanson,
    Gurkaynak-Sack-Swanson) -- free and excellent, and deliberately NOT used.
    They publish the *change* in the futures rate across a window bracketing the
    announcement. That is measured after the decision is known. Wiring it as a
    feature would be the worst look-ahead leak available to this repo.

HOW THE EXPECTED MOVE IS COMPUTED

The CME FedWatch calculation, which is arithmetic on the settlement price, not
a model. A ZQ contract settles to the *average* effective funds rate over its
delivery month, so for a meeting whose new target takes effect on day `e` of a
month with `n` days:

    R_month = (e-1)/n * r_before + (n-e+1)/n * r_after
    =>  r_after = (R_month - (e-1)/n * r_before) * n / (n-e+1)
    expected_move_bp = 100 * (r_after - r_before)

`r_before` is a trailing 10-day **median** of the effective funds rate as of the
feature cutoff -- see `_r_before`, where the reason is a bug that was real.

When the meeting lands so late in the month that fewer than seven days carry the
new rate, that division amplifies noise, so the **next** month's contract is used
instead and read directly as `r_after` -- exact unless another FOMC meeting falls
inside that month, which is rare. After the NS strip ends in 2015 there is no
historical next-month quote to reach for (expired contracts are purged from
Yahoo), so 37 of 293 scheduled meetings fall back to the proxy. `cli.py
provenance` prints that split; the README states the count.

POINT-IN-TIME DISCIPLINE

Every rate is indexed by the **delivery month it describes** and stamped with
the **observation date it was quoted on**, so `fred.latest_as_of` answers "what
did the market think about November, as of the day before the October meeting"
without any special-casing. A settlement dated D is stamped `pub = D`, the same
convention fred.py uses for DGS1 and every other daily close: it is known at
that day's close, which is the evening before the meeting opens. The Atlanta
Fed tracker is a *published report* rather than a raw print, so its rows are
stamped `pub = D + 1` -- one day stricter -- because the Bank posts a day's
estimates the following morning.

No LLM. No key. Class C.
"""

from __future__ import annotations

import calendar
import datetime
import io
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import fred

HERE = Path(__file__).resolve().parent
CACHE = HERE / "data" / "cache"
CACHE_TTL_S = 20 * 3600
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jarvis-fomc/1.0"}

NS_URL = "https://dataverse.harvard.edu/api/access/datafile/3157930"
NS_MEMBER = "Replication_Online/Stata/Data_Orig/fedfutures.csv"
NS_CITE = ("Nakamura & Steinsson (2018) replication archive, Harvard Dataverse "
           "doi:10.7910/DVN/HZOXKN, CC0 1.0")

YAHOO = ("https://query1.finance.yahoo.com/v8/finance/chart/"
         "%s?period1=0&period2=2000000000&interval=1d")
YAHOO_FRONT = "ZQ%3DF"
MONTH_CODE = "FGHJKMNQUVXZ"      # Jan..Dec, CME convention

# The Bank moved this file out of `cenfis/` in September 2026 and the old path
# began serving a 200-status HTML 404 page. Current path first, old one kept as
# a fallback in case the move is ever reverted; _cached_bytes rejects anything
# that is not actually a zip, so a moved file can no longer poison the cache.
MPT_URLS = (
    "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/"
    "research-and-data/data/market-probability-tracker/mpt_histdata.xlsx",
    "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/"
    "cenfis/market-probability-tracker/mpt_histdata.xlsx",
)
MPT_URL = MPT_URLS[0]
MPT_PAGE = "https://www.atlantafed.org/research-and-data/data/market-probability-tracker"
MPT_LICENCE = ("Federal Reserve Bank of Atlanta; CME Group market data used with "
               "permission. Personal and educational use only.")

PROXY = "100 * (DGS3MO - EFFR), 10-day average -- the spread the engine used before"

# Fewer effective days than this in the meeting month and the FedWatch division
# amplifies noise too much; roll to the next month's contract instead.
MIN_DAYS_AFTER = 7
# How many months of listed contracts to pull from Yahoo so the LIVE forecast
# has a strip rather than only the front month. Thirteen reaches the eighth
# meeting out plus the month after it, which is what watch.py's FedWatch table
# needs when a meeting lands too late in its month to weight.
LIVE_STRIP_MONTHS = 13

NS_XML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

_STATE: dict | None = None


# -- fetch helpers ----------------------------------------------------------


def _fetch(url: str, timeout: int = 300, tries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - network; retry on anything
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("fetch failed after %d tries: %s -- %s" % (tries, url, last))


def _cached_bytes(name: str, url, refresh: bool, timeout: int = 300,
                  magic: bytes | None = None) -> bytes:
    """Fetch-through cache. `url` may be one URL or a tuple tried in order.

    `magic` is the leading bytes a real payload must start with (b"PK" for a
    zip / xlsx). A publisher that moves a file often keeps answering 200 with an
    HTML "not found" page; without this check that page was written over the
    last good copy and every later read failed with "File is not a zip file".
    """
    path = CACHE / name

    def _ok(b: bytes) -> bool:
        return magic is None or b[:len(magic)] == magic

    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_S:
        blob = path.read_bytes()
        if _ok(blob):
            return blob
    urls = (url,) if isinstance(url, str) else tuple(url)
    blob, err = None, None
    for u in urls:
        try:
            got = _fetch(u, timeout=timeout)
        except RuntimeError as e:
            err = e
            continue
        if _ok(got):
            blob = got
            break
        err = RuntimeError("%s did not return a %r payload" % (u, magic))
    if blob is None:
        if path.exists() and _ok(path.read_bytes()):
            # A stale copy beats silently degrading to the proxy without saying so.
            print("  futures: fetch failed for %s (%s); using cached copy" % (name, err), file=sys.stderr)
            return path.read_bytes()
        raise RuntimeError(str(err))
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return blob


def _ym(iso: str) -> str:
    return iso[:7]


def _ym_shift(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    t = (y * 12 + m - 1) + k
    return "%04d-%02d" % (t // 12, t % 12 + 1)


# -- source 1: Nakamura-Steinsson strip, 1989-2015 --------------------------


def _ns_rows(refresh: bool = False) -> list[tuple[str, str, float]]:
    """(observation date, delivery month, implied rate %) from the 7-contract strip.

    Column semantics, read off the archive's own values rather than assumed:
    FFE1C is the contract for the *current* calendar month, and FFB(k) equals
    FFE(k+1), which is the contract k months ahead. The two families are the same
    strip under Datastream's two roll conventions.
    """
    blob = _cached_bytes("ns_replication.zip", NS_URL, refresh, timeout=600,
                        magic=b"PK")
    text = zipfile.ZipFile(io.BytesIO(blob)).read(NS_MEMBER).decode("utf-8", "replace")
    lines = text.splitlines()
    head = lines[0].split(",")
    col = {h.strip(): i for i, h in enumerate(head)}
    wanted = {0: "FFE1C.D7"}
    for k in range(1, 8):
        wanted[k] = "FFB%dC.D7" % k
    out = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            mm, dd, yy = parts[0].split("/")
            day = "%04d-%02d-%02d" % (int(yy), int(mm), int(dd))
        except ValueError:
            continue
        for k, name in wanted.items():
            j = col.get(name)
            if j is None or j >= len(parts):
                continue
            raw = parts[j].strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                price = float(raw)
            except ValueError:
                continue
            out.append((day, _ym_shift(_ym(day), k), 100.0 - price))
    return out


# -- source 2: Yahoo, 2000 -> today -----------------------------------------


def _yahoo_closes(symbol: str, refresh: bool = False) -> list[tuple[str, float]]:
    name = "yahoo_%s.json" % symbol.replace("%3D", "=").replace("=", "-").replace(".", "_")
    blob = _cached_bytes(name, YAHOO % symbol, refresh, timeout=120)
    try:
        d = json.loads(blob.decode("utf-8"))
        res = d["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
    except (ValueError, KeyError, TypeError, IndexError):
        return []
    out = []
    for t, c in zip(stamps, closes):
        if c is None:
            continue
        out.append((datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                    float(c)))
    return out


def _yahoo_rows(refresh: bool = False) -> list[tuple[str, str, float]]:
    """Front contract for the whole live period, plus the listed forward strip.

    The front series covers the delivery month equal to its own observation
    month. The per-contract symbols are what let a *live* forecast reach a
    meeting in a later month; they only exist while a contract is listed, so
    they add depth to the recent past and nothing to the distant past.

    PER-CONTRACT ROWS COME FIRST, and the current month's own contract (k=0) is
    pulled too. `ZQ=F` is a continuous series and Yahoo rolls it early: on
    2026-10-02 it printed 96.065, which is the November contract to the tick,
    while ZQV26 (October) printed 96.1175. Stamped as October and written first,
    that roll leaked November's price into October. Where the contract itself is
    quoted it is the definition of the delivery month, so it wins; the front
    series still fills every day before per-contract history exists.
    """
    rows: list[tuple[str, str, float]] = []
    today = datetime.date.today()
    for k in range(0, LIVE_STRIP_MONTHS + 1):
        t = (today.year * 12 + today.month - 1) + k
        y, m = t // 12, t % 12 + 1
        sym = "ZQ%s%02d.CBT" % (MONTH_CODE[m - 1], y % 100)
        ym = "%04d-%02d" % (y, m)
        try:
            closes = _yahoo_closes(sym, refresh)
        except RuntimeError:
            continue          # a contract not yet listed is normal, not an error
        for d, p in closes:
            rows.append((d, ym, 100.0 - p))
    rows += [(d, _ym(d), 100.0 - p) for d, p in _yahoo_closes(YAHOO_FRONT, refresh)]
    return rows


# -- source 3: Atlanta Fed option-implied probabilities ---------------------


def _mpt_rows(refresh: bool = False) -> list[dict]:
    path = CACHE / "mpt.atlantafed.json"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_S:
        return json.loads(path.read_text(encoding="utf-8"))

    by_day = _mpt_by_day(refresh)
    out = []
    for day in sorted(by_day):
        ahead = sorted(w for w in by_day[day] if w[0] > day)
        if not ahead:
            continue
        _window, rec = ahead[0]      # the nearest window still in front of us
        out.append({"date": day,
                    "p_cut": rec.get("Prob: cut"),
                    "p_hike": rec.get("Prob: hike")})
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


def mpt_windows(when: str, refresh: bool = False) -> dict | None:
    """Every option window the tracker published, from its last report visible at `when`.

    {"date": report day, "windows": [{"window", "p_cut", "p_hike"}, ...]}.
    p_cut / p_hike are P(the target range at that date sits below / above the
    range prevailing on the report day). watch.py sets these beside the futures
    tree as an independent read: options carry the tails a two-outcome split of
    the futures mean cannot. Same `pub = D + 1` stamp as the feature columns.
    """
    path = CACHE / "mpt.windows.json"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_S:
        by_day = json.loads(path.read_text(encoding="utf-8"))
    else:
        by_day = {d: [{"window": w, "p_cut": r.get("Prob: cut"), "p_hike": r.get("Prob: hike")}
                      for w, r in sorted(ws) if w > d]
                  for d, ws in _mpt_by_day(refresh).items()}
        # Only the recent tail is ever read live; keep the cache small.
        keep = sorted(by_day)[-30:]
        by_day = {d: by_day[d] for d in keep}
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(by_day), encoding="utf-8")
    vis = [d for d in by_day if fred.shift_days(d, 1) <= when]
    if not vis:
        return None
    day = max(vis)
    return {"date": day, "windows": by_day[day]}


def _mpt_by_day(refresh: bool = False) -> dict[str, list]:
    """{report day: [(window date, {field: probability}), ...]} off the workbook."""
    blob = _cached_bytes("mpt_histdata.xlsx", MPT_URLS, refresh, timeout=300,
                        magic=b"PK")
    z = zipfile.ZipFile(io.BytesIO(blob))

    strings: list[str] = []
    for _, el in ET.iterparse(io.BytesIO(z.read("xl/sharedStrings.xml"))):
        if el.tag == NS_XML + "si":
            strings.append("".join(t.text or "" for t in el.iter(NS_XML + "t")))
            el.clear()

    # Resolve the DATA sheet by name -- a reordered workbook must not read the
    # licence sheet and call it data.
    book = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid = {r.get("Id"): r.get("Target") for r in rels}
    target = None
    for sh in book.find(NS_XML + "sheets"):
        key = sh.get("{http://schemas.openxmlformats.org/officeDocument/"
                     "2006/relationships}id")
        if (sh.get("name") or "").strip().upper() == "DATA":
            target = rid.get(key)
    if not target:
        raise RuntimeError("Atlanta Fed MPT: no DATA sheet in the workbook")
    if not target.startswith("xl/"):
        target = "xl/" + target.lstrip("/")

    keep = ("Prob: cut", "Prob: hike")
    acc: dict[tuple, dict] = {}
    for _, row in ET.iterparse(io.BytesIO(z.read(target))):
        if row.tag != NS_XML + "row":
            continue
        vals = []
        for c in row:
            v = c.find(NS_XML + "v")
            val = None if v is None else v.text
            if c.get("t") == "s" and val is not None:
                val = strings[int(val)]
            vals.append(val)
        row.clear()
        if len(vals) < 5:
            continue
        day, ref, _range, field, value = vals[:5]
        if not day or not day.startswith("20") or field not in keep:
            continue
        try:
            acc.setdefault((day, ref), {})[field] = float(value) / 100.0
        except (TypeError, ValueError):
            continue

    by_day: dict[str, list] = {}
    for (day, ref), rec in acc.items():
        try:
            window = (datetime.date(1899, 12, 30)
                      + datetime.timedelta(days=int(float(ref)))).isoformat()
        except (TypeError, ValueError):
            continue
        by_day.setdefault(day, []).append((window, rec))
    return by_day


# -- assembly ---------------------------------------------------------------


def load(refresh: bool = False) -> dict:
    """Build (and memoise) every panel this module serves.

    `by_month[YYYY-MM]` is a fred.Panel of the implied rate for that delivery
    month, indexed by the date it was quoted. That shape is what makes the
    point-in-time question trivial: "the market's view of November, as known on
    the 27th of October" is one binary search, and a quote can never be visible
    before the day it printed.
    """
    global _STATE
    if _STATE is not None and not refresh:
        return _STATE

    # This block is ON by default, so a dead upstream must degrade rather than
    # take the whole engine down with it. Each source is caught separately and
    # the loss is printed, not swallowed: whatever still loads is used, and the
    # DGS3MO-EFFR proxy covers the rest exactly as it did before any of this
    # existed. Silence here would mean the model quietly got worse.
    try:
        rows = _ns_rows(refresh=refresh)
    except Exception as e:  # noqa: BLE001
        print("  futures: Nakamura-Steinsson archive unavailable (%s);"
              " pre-2000 falls back to the proxy" % e, file=sys.stderr)
        rows = []
    ns_last = max((d for d, _, _ in rows), default="0000-00-00")
    try:
        yahoo = _yahoo_rows(refresh=refresh)
    except Exception as e:  # noqa: BLE001
        print("  futures: Yahoo ZQ=F unavailable (%s); post-%s falls back to"
              " the proxy" % (e, ns_last), file=sys.stderr)
        yahoo = []
    # NS is the authority where it reaches -- it is the full strip and it is
    # CC0. Yahoo fills everything after it, and the overlap is kept for the
    # validation test rather than thrown away.
    rows += [r for r in yahoo if r[0] > ns_last]

    # One quote per (delivery month, day). Sources can legitimately overlap --
    # once the calendar rolls into a delivery month, the front series and that
    # month's own listed contract are the same contract quoted twice, except
    # when Yahoo has rolled the front early (see _yahoo_rows). First writer
    # wins, which is NS, then per-contract, then the front series.
    by_month: dict[str, dict] = {}
    for day, ym, rate in rows:
        by_month.setdefault(ym, {}).setdefault(day, rate)
    panels = {}
    for ym, days in by_month.items():
        panels[ym] = fred.Panel(fred.Obs(d, days[d], d) for d in sorted(days))

    try:
        mpt = _mpt_rows(refresh=refresh)
    except Exception as e:  # noqa: BLE001
        print("  futures: Atlanta Fed tracker unavailable (%s);"
              " ff_p_cut / ff_p_hike will be null throughout" % e, file=sys.stderr)
        mpt = []

    def _prob(kind):
        return fred.Panel(
            fred.Obs(r["date"], float(r[kind]), fred.shift_days(r["date"], 1))
            for r in mpt if r.get(kind) is not None)

    bill = fred.series("DGS3MO", vintage=False, refresh=refresh)
    eff = fred.series("DFF", vintage=False, refresh=refresh)
    ffmap = {o.date: o.value for o in eff}
    proxy = fred.Panel(fred.Obs(o.date, 100.0 * (o.value - ffmap[o.date]), o.date)
                       for o in bill if o.date in ffmap)

    quotes = sorted({d for d, _, _ in rows})
    _STATE = {
        "by_month": panels,
        "p_cut": _prob("p_cut"),
        "p_hike": _prob("p_hike"),
        "proxy": proxy,
        "effr": eff,
        "meta": {
            "strip_from": quotes[0] if quotes else None,
            "strip_to": quotes[-1] if quotes else None,
            "n_quote_days": len(quotes),
            "ns_cite": NS_CITE,
            "ns_from": min((d for d, _, _ in rows if d <= ns_last), default=None),
            "ns_to": ns_last,
            "yahoo_from": min((d for d, _, _ in yahoo), default=None),
            "yahoo_to": max((d for d, _, _ in yahoo), default=None),
            "mpt_source": "Atlanta Fed Market Probability Tracker",
            "mpt_url": MPT_PAGE,
            "mpt_licence": MPT_LICENCE,
            "mpt_from": mpt[0]["date"] if mpt else None,
            "mpt_to": mpt[-1]["date"] if mpt else None,
            "proxy": PROXY,
            "proxy_from": proxy[0].date if proxy else None,
        },
    }
    return _STATE


# -- point-in-time interface ------------------------------------------------


def _rate_for_month(st: dict, ym: str, when: str) -> tuple:
    """(implied rate for delivery month `ym`, the date it was quoted) as of `when`."""
    panel = st["by_month"].get(ym)
    if panel is None:
        return None, None
    o = fred.latest_as_of(panel, when)
    return (o.value, o.date) if o else (None, None)


def _r_before(st: dict, when: str, n: int = 10) -> float | None:
    """The prevailing effective funds rate as of `when`: a MEDIAN, not a mean.

    This is not fussiness. The effective rate spikes on the last day of a month
    and collapses over the turn of the year -- on 2000-12-29 it printed 5.41
    against a 6.50 target, and on 1994-01-31 it printed 3.68 against 3.00. A
    trailing mean drags those settlement artefacts straight into `r_before`, and
    since the FedWatch formula subtracts `r_before` from a contract rate, a 70bp
    artefact becomes a 70bp phantom "priced move". Before this was a median, the
    January 2001 intermeeting CUT read as +61bp of priced HIKE.

    A median over ten business days ignores one to three spike days entirely and
    still tracks a genuine policy change within a couple of weeks.
    """
    vis = fred.as_of(st["effr"], when)
    if len(vis) < n:
        return None
    vals = sorted(o.value for o in vis[-n:])
    return vals[len(vals) // 2]


def expected_move_bp(when: str, effective: str, refresh: bool = False) -> tuple:
    """(basis points priced for a decision effective on `effective`, source).

    source is 'futures' when it came off the strip and 'proxy' when it fell back
    to DGS3MO-EFFR. Returns (None, 'none') if neither reaches.
    """
    st = load(refresh=refresh)
    r_before = _r_before(st, when)
    if r_before is None:
        return None, "none"

    d = datetime.date.fromisoformat(effective)
    n = calendar.monthrange(d.year, d.month)[1]
    days_before = d.day - 1
    days_after = n - days_before

    if days_after >= MIN_DAYS_AFTER:
        rate, _q = _rate_for_month(st, "%04d-%02d" % (d.year, d.month), when)
        if rate is not None:
            r_after = (rate - (days_before / n) * r_before) * n / days_after
            return 100.0 * (r_after - r_before), "futures"

    # Too few days left in the meeting month for the weighting to be stable, or
    # no quote for it. The next month's contract is the post-meeting rate
    # outright -- exact unless another meeting lands inside that month.
    nxt = _ym_shift("%04d-%02d" % (d.year, d.month), 1)
    rate, _q = _rate_for_month(st, nxt, when)
    if rate is not None:
        return 100.0 * (rate - r_before), "futures"

    proxy = fred.avg_window(st["proxy"], when, 10)
    return (proxy, "proxy") if proxy is not None else (None, "none")


def move_probability(when: str, days: int = 10, refresh: bool = False) -> tuple:
    """(P(cut), P(hike)) from option-implied distributions, or (None, None).

    There is deliberately no proxy. A Treasury-minus-funds spread is not a
    probability, and a probability derived from the futures mean under a
    two-outcome assumption would only be a monotone restatement of
    expected_move_bp -- no new information, dressed up as new information.
    """
    st = load(refresh=refresh)
    return (fred.avg_window(st["p_cut"], when, days),
            fred.avg_window(st["p_hike"], when, days))


def provenance(refresh: bool = False) -> dict:
    return dict(load(refresh=refresh)["meta"])


def overlap_check(refresh: bool = False) -> dict:
    """Yahoo's front contract against the NS front contract, day by day.

    The splice is the one place this module could be quietly wrong, so the
    agreement is measured rather than asserted. test_fomc.py calls this.
    """
    ns = {}
    for day, ym, rate in _ns_rows(refresh=refresh):
        if ym == _ym(day):
            ns[day] = rate
    y = {d: 100.0 - p for d, p in _yahoo_closes(YAHOO_FRONT, refresh=refresh)}
    both = sorted(set(ns) & set(y))
    if not both:
        return {"n": 0, "from": None, "to": None, "mean_bp": float("nan"),
                "sd_bp": float("nan"), "median_bp": float("nan"),
                "share_over_5bp": float("nan")}
    diffs = sorted(100.0 * (y[d] - ns[d]) for d in both)     # in bp
    mean = sum(diffs) / len(diffs)
    sd = (sum((x - mean) ** 2 for x in diffs) / len(diffs)) ** 0.5
    return {"n": len(both), "from": both[0], "to": both[-1],
            "mean_bp": mean, "sd_bp": sd,
            "median_bp": diffs[len(diffs) // 2],
            "share_over_5bp": sum(1 for x in diffs if abs(x) > 5) / len(diffs)}


if __name__ == "__main__":
    import sys

    st = load(refresh="--refresh" in sys.argv)
    print(json.dumps(st["meta"], indent=1))
    print("\n  splice check (Yahoo front vs Nakamura-Steinsson front):")
    print("   ", json.dumps(overlap_check(), indent=1).replace("\n", "\n    "))
    print("\n  %-12s %-12s %12s %-9s %8s %8s"
          % ("cutoff", "effective", "exp move bp", "source", "P(cut)", "P(hike)"))
    for when, eff in (("1990-02-06", "1990-02-08"), ("1994-02-03", "1994-02-05"),
                      ("2001-01-02", "2001-01-04"), ("2008-12-15", "2008-12-17"),
                      ("2015-12-15", "2015-12-17"), ("2019-07-30", "2019-08-01"),
                      ("2022-06-14", "2022-06-16"), ("2024-09-17", "2024-09-19"),
                      ("2026-10-27", "2026-10-29")):
        bp, src = expected_move_bp(when, eff)
        c, h = move_probability(when)
        print("  %-12s %-12s %12s %-9s %8s %8s"
              % (when, eff, "n/a" if bp is None else "%+.1f" % bp, src,
                 "n/a" if c is None else "%.2f" % c,
                 "n/a" if h is None else "%.2f" % h))
