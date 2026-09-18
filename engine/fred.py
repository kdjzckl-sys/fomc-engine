"""FRED / ALFRED client for the FOMC engine.

Two things make this different from a plain FRED wrapper:

1. **Vintage awareness.** Macro data is revised. Backtesting a Fed reaction
   function on today's revised CPI is look-ahead bias -- the Committee never saw
   those numbers. Every revisable series is pulled with ALFRED `output_type=4`
   (initial release only), which stamps each observation with the date it was
   first published (`realtime_start`). The feature builder then filters on
   `pub <= meeting_date`, so a meeting only ever sees what had actually printed.
   Series that are never revised (market rates, the policy target itself) skip
   this and use the plain series.

2. **Disk cache.** One JSON per (series, mode) under data/cache/. FRED allows
   120 req/min; the whole universe is ~30 calls, so a cold build is one burst
   and every later run is free. `--refresh` busts it.

No LLM anywhere in this file. Class C.
"""

from __future__ import annotations

import json
import os
import sys
import time
import bisect
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, NamedTuple

HERE = Path(__file__).resolve().parent
CACHE = HERE / "data" / "cache"
BASE = "https://api.stlouisfed.org/fred/series/observations"

# Cache entries older than this are refetched on the next build. Macro series
# print at most weekly; a day of staleness costs nothing and saves the burst.
CACHE_TTL_S = 20 * 3600


class Obs(NamedTuple):
    """One observation. `pub` is when it first became public (vintage mode)."""

    date: str          # observation date (the period the number describes)
    value: float
    pub: str           # publication date; == date for never-revised series


class Panel(list):
    """A series plus a sorted "known by" index, so as_of() is a binary search.

    Building the design matrix asks ~60 point-in-time questions per meeting
    across 360 meetings, and several series are 14k daily observations long.
    Linear scanning that is ~200M string comparisons and takes minutes; the
    index takes it to milliseconds.

    The index key is max(pub, date): a number is known once it has been
    published AND its reference period has arrived. The second half only binds
    on projection series (CBO's NROU is published now with observations dated
    2036), but it has to be in the key or the search would not be monotone.
    """

    __slots__ = ("keys",)

    def __init__(self, obs):
        super().__init__(obs)
        keys, hi = [], ""
        for o in self:
            hi = max(hi, o.pub, o.date)   # force monotone; the data is near-sorted
            keys.append(hi)
        self.keys = keys

    def upto(self, when: str) -> int:
        """Count of observations known at `when`."""
        return bisect.bisect_right(self.keys, when)


# -- key resolution ---------------------------------------------------------


def _read_env_file(path: Path, name: str) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("'").strip('"')
    except OSError:
        return None
    return None


def api_key() -> str:
    """FRED key from the environment, else a .env file beside the repo.

    Environment first, so CI and a scheduled run need no file on disk. The
    repo-root .env is the local-development convenience; it is gitignored and
    the only secret this project has.
    """
    k = os.environ.get("FRED_API_KEY", "").strip()
    if k:
        return k
    root = HERE.parents[1]  # engine/ -> repo root
    for base in (root, HERE):
        for name in (".env.local", ".env"):
            k = _read_env_file(base / name, "FRED_API_KEY") or ""
            if k:
                return k
    raise SystemExit(
        "No FRED_API_KEY. Set it in the environment, or copy .env.example to "
        ".env at the repo root and put the key there "
        "(free key: fred.stlouisfed.org/docs/api/api_key.html)"
    )


# -- fetch ------------------------------------------------------------------


def _get(params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jarvis-fomc/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - network; retry on anything
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("FRED fetch failed after 4 tries: " + str(last))


def _raw(series_id: str, vintage: bool, start: str) -> list[Obs]:
    params = {
        "series_id": series_id,
        "api_key": api_key(),
        "file_type": "json",
        "observation_start": start,
        "sort_order": "asc",
    }
    if vintage:
        params["output_type"] = 4               # initial release only
        params["realtime_start"] = "1776-07-04"
        params["realtime_end"] = "9999-12-31"

    out: list[Obs] = []
    for o in _get(params).get("observations", []):
        v = o.get("value", ".")
        if v in (".", "", None):
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        pub = (o.get("realtime_start") or "") if vintage else o["date"]
        if not pub or pub.startswith("1776"):
            pub = ""                            # no genuine vintage stamp
        out.append(Obs(o["date"], val, pub))
    return out


def series(series_id: str, *, vintage: bool = True, refresh: bool = False,
           start: str = "1965-01-01") -> list[Obs]:
    """Observations for `series_id`, oldest first.

    vintage=False -> the current series; `pub` == observation date. Correct only
    for things that are never revised: market rates, the policy target itself.

    vintage=True -> real publication dates, spliced. ALFRED's initial-release
    record does not reach all the way back -- coverage starts around 1997 for
    CPI, 2000 for core PCE, 2014 for Case-Shiller and real GDP growth. Asking
    for initial releases alone silently truncates half the sample, which is how
    a "1990-2026 backtest" quietly becomes a 2014-2026 one.

    So: use genuine vintages where ALFRED has them, and for older observations
    fall back to the revised value stamped with this series' own **measured**
    median publication lag (from the vintage portion). That keeps the timing
    honest -- a 1994 meeting still cannot see a number that had not printed --
    while accepting revised *values* in the early years.

    The cost is real and bounded: in the pre-ALFRED segment the model sees
    slightly cleaner data than the Committee did, which flatters the early
    backtest. It does not touch the modern period, which is what the live
    prediction runs on. `cli.py provenance` prints the split date per series.
    """
    mode = "vint" if vintage else "curr"
    cache_path = CACHE / (series_id + "." + mode + ".json")
    if not refresh and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < CACHE_TTL_S:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            return Panel(Obs(*o) for o in raw)

    if not vintage:
        out = [o for o in _raw(series_id, False, start)]
    else:
        vint = [o for o in _raw(series_id, True, start) if o.pub]
        lag = _median_lag(vint)
        first = vint[0].date if vint else "9999-12-31"
        # revised values for everything ALFRED's vintage record does not reach
        back = [Obs(o.date, o.value, _shift(o.date, lag))
                for o in _raw(series_id, False, start) if o.date < first]
        out = back + vint

    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps([list(o) for o in out]), encoding="utf-8")
    return Panel(out)


def _median_lag(obs: list[Obs], default: int = 45) -> int:
    """Median days between an observation's period and its first publication.

    Measured, not assumed: CPI lands ~45 days after the 1st of its month, GDP
    ~120 days after the start of its quarter, weekly claims ~5 days. Using the
    series' own lag keeps the back-filled segment on the same release clock as
    the vintage segment instead of a made-up constant.
    """
    lags = []
    for o in obs:
        try:
            d0 = datetime.strptime(o.date, "%Y-%m-%d").date()
            d1 = datetime.strptime(o.pub, "%Y-%m-%d").date()
        except ValueError:
            continue
        gap = (d1 - d0).days
        if 0 <= gap <= 400:
            lags.append(gap)
    if not lags:
        return default
    lags.sort()
    return lags[len(lags) // 2]


def _shift(iso: str, days: int) -> str:
    return (datetime.strptime(iso, "%Y-%m-%d").date() + timedelta(days=days)).isoformat()


def provenance(series_id: str, vintage: bool = True) -> dict:
    """Where this series' genuine-vintage record begins, and how much is back-filled.

    Surfaced by `cli.py provenance` so the honest caveat is one command away
    rather than a paragraph nobody reads.
    """
    obs = series(series_id, vintage=vintage)
    if not obs:
        return {"series": series_id, "n": 0}
    if not vintage:
        return {"series": series_id, "n": len(obs), "vintage_from": None,
                "backfilled": 0, "mode": "never revised"}
    # _median_lag walks the whole series, so it is computed ONCE. It used to be
    # called inside the scan below, which made this O(n^2): on DFF (14k daily
    # observations) `cli.py provenance` never finished, and a documented front
    # door that hangs is a broken one.
    lag = _median_lag(obs)
    first_real = None
    for o in obs:
        # the back-filled block sits at the front and shares one exact lag
        if o.pub != _shift(o.date, lag):
            first_real = o.date
            break
    back = sum(1 for o in obs if first_real and o.date < first_real)
    return {"series": series_id, "n": len(obs), "first": obs[0].date,
            "vintage_from": first_real, "backfilled": back,
            "median_lag_days": lag,
            "mode": "spliced" if back else "full vintage"}


# -- point-in-time lookup ---------------------------------------------------


def as_of(obs: Iterable[Obs], when: str) -> list[Obs]:
    """Everything known at `when`, oldest first. The point-in-time gate.

    Two conditions, and both matter. `pub <= when` is the obvious one: the number
    had to have printed. `date <= when` is the one that bites on projection
    series -- CBO's NROU is published today with observations dated 2036, and
    without this guard a 1995 meeting would be handed a forecast of the 2030s.
    """
    if isinstance(obs, Panel):
        return obs[:obs.upto(when)]
    return [o for o in obs if o.pub <= when and o.date <= when]


def latest_as_of(obs: Iterable[Obs], when: str) -> Obs | None:
    if isinstance(obs, Panel):
        n = obs.upto(when)
        return obs[n - 1] if n else None
    vis = as_of(obs, when)
    return vis[-1] if vis else None


def value_n_back(obs: Iterable[Obs], when: str, n: int) -> float | None:
    """Value n observations before the latest one visible at `when`."""
    if isinstance(obs, Panel):
        k = obs.upto(when)
        return obs[k - 1 - n].value if k > n else None
    vis = as_of(obs, when)
    if len(vis) <= n:
        return None
    return vis[-1 - n].value


def yoy(obs: Iterable[Obs], when: str, periods: int = 12) -> float | None:
    """Percent change over `periods` observations, as of `when`."""
    k = obs.upto(when) if isinstance(obs, Panel) else len(as_of(obs, when))
    if k <= periods:
        return None
    now, then = obs[k - 1].value, obs[k - 1 - periods].value
    if then == 0:
        return None
    return (now / then - 1.0) * 100.0


def annualized(obs: Iterable[Obs], when: str, periods: int) -> float | None:
    """Annualized percent change over `periods` monthly observations."""
    k = obs.upto(when) if isinstance(obs, Panel) else len(as_of(obs, when))
    if k <= periods:
        return None
    now, then = obs[k - 1].value, obs[k - 1 - periods].value
    if then <= 0 or now <= 0:
        return None
    return ((now / then) ** (12.0 / periods) - 1.0) * 100.0


def avg_window(obs: Iterable[Obs], when: str, days: int) -> float | None:
    """Mean of the last `days` of observations available at `when`.

    The window is anchored to the **most recent observation known at `when`**,
    not to `when` itself. That distinction is the difference between working and
    not working on a live prediction: the next meeting is weeks in the future, so
    a window ending on the meeting date contains no observations at all and every
    daily market feature -- the most informative block in the matrix -- would come
    back None and get median-imputed into neutrality.

    Anchoring gives "the 10-day average as of the latest print", which is what a
    desk means by it. On historical rows a daily series always has observations
    within the window, so this changes nothing there; it only rescues future
    dates and long gaps.
    """
    if isinstance(obs, Panel):
        hi = obs.upto(when)
        if not hi:
            return None
        cut = shift_days(obs[hi - 1].date, -days)
        vals = []
        for i in range(hi - 1, -1, -1):
            if obs[i].date < cut:
                break
            vals.append(obs[i].value)
    else:
        vis = [o for o in obs if o.pub <= when and o.date <= when]
        if not vis:
            return None
        cut = shift_days(vis[-1].date, -days)
        vals = [o.value for o in vis if o.date >= cut]
    return sum(vals) / len(vals) if vals else None


@lru_cache(maxsize=65536)
def shift_days(iso: str, n: int) -> str:
    """Date arithmetic on ISO strings. Cached because the feature builder asks
    the same few hundred (date, offset) questions thousands of times, and
    strptime is expensive enough that it dominated the profile before this."""
    y, m, d = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
    return (date(y, m, d) + timedelta(days=n)).isoformat()


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "DFEDTARU"
    vint = "--vintage" in sys.argv
    o = series(sid, vintage=vint)
    print("%s: %d obs, %s -> %s, last=%s (pub %s)"
          % (sid, len(o), o[0].date, o[-1].date, o[-1].value, o[-1].pub))
