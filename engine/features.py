"""The reaction function, expressed as features a model can read.

Everything here is computed **as of the morning the meeting opens**. Nothing that
printed after the meeting can reach the feature vector -- fred.as_of() enforces
that on every single lookup. That is the whole reason this file is tedious.

Six blocks, each answering a question the Committee actually asks:

  policy    Where is the rate now, relative to where a rule says it should be,
            and which way have we been moving? The Fed is path-dependent to an
            unusual degree -- it moves in runs and almost never reverses inside
            two meetings. `last_dir` and `run_length` carry most of that.
  inflation Both mandates' price half: core PCE (the target), core CPI, the
            momentum in each, and what markets expect. Level *and* direction --
            the Committee reacts to 3-month momentum long before the 12-month
            rate confirms it.
  labor     The employment half: unemployment, its gap to the natural rate, the
            Sahm gap (the recession trigger the 2024 cutting cycle was argued
            over), payroll momentum, claims, and labor tightness.
  growth    GDP, industrial production, real retail sales, consumer sentiment.
  housing   The rate-sensitive sector that turns first, and the one the request
            singled out. Permits lead starts lead sales lead prices; months'
            supply is the inventory overhang; the mortgage spread is how much of
            a policy move is actually reaching households. Housing is the
            transmission mechanism, so it leads the cycle by roughly 2-4 quarters.
  markets   What is already priced, and what financial conditions are doing.
            (DGS1 - EFFR) is the single most informative column in the matrix:
            it is the market's expected policy path over the next year, and the
            Fed rarely surprises it at a scheduled meeting.

On fed funds futures. They used to be absent entirely, with the Treasury-vs-EFFR
spreads standing in for them -- the engine's biggest stated weakness.
`futures.py` now supplies the real thing: the CME ZQ strip back to 1989, run
through the FedWatch arithmetic (`ff_exp_move_bp`), plus option-implied cut and
hike probabilities from 2023 (`ff_p_cut`, `ff_p_hike`). It still falls back to
the old proxy for 37 late-month meetings after 2015 and for everything before
1989, and `cli.py provenance` prints exactly where.

These columns are ON by default because the walk-forward record says they earn
it -- every headline metric improves and the baselines are untouched. Run
`--no-futures` to get the old feature set back and check that claim; it writes
to separate artefacts and cannot overwrite the published ones.
"""

from __future__ import annotations

import os

import fred

# The futures block is ON by default, because the walk-forward record says it
# earns its place: it improves every headline metric on every window tested and
# leaves all three baselines untouched (they are properties of the same rows).
# `FOMC_FUTURES=0`, or `cli.py <cmd> --no-futures`, turns it off and writes to
# its own *.noff artefacts, so the claim stays falsifiable in one command.
def _on(var: str, default: str) -> bool:
    return os.environ.get(var, default).strip().lower() not in ("0", "false", "no")


FUTURES = _on("FOMC_FUTURES", "1")

# Three more blocks, each switchable the same way and each measured against the
# walk-forward record before its default was set (engine/README.md, "Beyond the
# macro panel"):
#   NEWS   news sentiment + news-based policy uncertainty      (news.py)
#   TEXT   the Committee's own words: prior statement + dots   (text/derive.py)
#   SHOCK  fast-moving stress: spikes, crashes, jumps          (this file)
NEWS = _on("FOMC_NEWS", "0")
TEXT = _on("FOMC_TEXT", "0")
SHOCK = _on("FOMC_SHOCK", "0")

# The published configuration. Any deviation from it writes to its own tagged
# artefacts (matrix.<tag>.json, backtest.<tag>.json, ...) so a comparison run
# can never overwrite the files the scorecard and the dashboard read.
_PUBLISHED = {"ff": True, "news": False, "text": False, "shock": False}
_STATE = {"ff": FUTURES, "news": NEWS, "text": TEXT, "shock": SHOCK}
ARTIFACT_TAG = "-".join(("no" if not on else "with") + k
                        for k, on in _STATE.items() if on != _PUBLISHED[k])


def artifact(stem: str) -> str:
    """'matrix' -> 'matrix.json' on the published config, else 'matrix.<tag>.json'."""
    return stem + (".%s.json" % ARTIFACT_TAG if ARTIFACT_TAG else ".json")


if FUTURES:
    import futures as ff_source
if NEWS or SHOCK:
    import news as news_source
if TEXT:
    import sys
    from pathlib import Path
    # Appended, not inserted: text/ has its own cli.py, and the engine's must win.
    _TEXT_DIR = str(Path(__file__).resolve().parent / "text")
    if _TEXT_DIR not in sys.path:
        sys.path.append(_TEXT_DIR)

# -- the series universe ----------------------------------------------------
# vintage=True  -> revisable; use ALFRED initial releases + publication dates.
# vintage=False -> a market print or an announcement; never revised.

SERIES = {
    # prices
    "CPIAUCSL": True, "CPILFESL": True, "PCEPI": True, "PCEPILFE": True,
    # labour
    "UNRATE": True, "PAYEMS": True, "ICSA": True, "NROU": True, "JTSJOL": True,
    "UNEMPLOY": True, "CIVPART": True,
    # activity
    "INDPRO": True, "RRSFS": True, "UMCSENT": True, "A191RL1Q225SBEA": True,
    # housing
    "HOUST": True, "PERMIT": True, "HSN1F": True, "MSACSR": True,
    "CSUSHPINSA": True, "MORTGAGE30US": False,
    # rates / markets
    "DFF": False, "DGS3MO": False, "DGS1": False, "DGS2": False, "DGS10": False,
    "T10Y2Y": False, "T10Y3M": False, "T5YIE": False, "DFII10": False,
    "BAA10Y": False, "VIXCLS": False, "NFCI": False, "NASDAQCOM": False,
    "DCOILWTICO": False,
}

# Column order is fixed here and nowhere else. The model file stores it so a
# saved model can never be fed a differently-ordered vector.
_CORE_COLUMNS = [
    # policy state
    "level", "real_rate", "taylor_gap", "chg_12m", "months_since_move",
    "last_dir", "run_length", "zlb",
    # inflation
    "core_pce_yoy", "core_pce_gap", "core_pce_3m", "core_pce_accel",
    "core_cpi_yoy", "cpi_yoy", "breakeven_5y", "real_10y",
    # labour
    "unrate", "unrate_gap", "unrate_chg_6m", "sahm_gap",
    "payrolls_3m", "payrolls_chg", "claims_yoy", "vacancy_ratio", "partic_chg",
    # growth
    "gdp_qoq", "indpro_yoy", "retail_yoy", "sentiment", "sentiment_chg",
    # housing
    "starts_yoy", "starts_3m", "permits_yoy", "permits_lead",
    "newsales_yoy", "months_supply", "hpi_yoy", "mortgage_spread", "mortgage_chg_6m",
    # markets / expectations
    "mkt_3m", "mkt_1y", "mkt_2y", "slope_10y2y", "slope_10y3m",
    "d_2y_intermeeting", "credit_spread", "credit_chg", "vix", "nfci",
    "equity_3m", "equity_drawdown", "oil_yoy",
]

# Market-implied policy expectations from futures/options (futures.py).
# Appended only when FUTURES is on, so the default column order -- and every
# model saved against it -- is byte-identical to what it was before.
FUTURES_COLUMNS = ["ff_exp_move_bp", "ff_p_cut", "ff_p_hike"]

# News: the level and direction of economic news tone, and of news-measured
# policy uncertainty. Both series count newspaper text; see news.py for what is
# and is not point-in-time about them.
NEWS_COLUMNS = ["news_sent", "news_sent_chg", "news_sent_intermeeting",
                "epu", "epu_chg"]

# The Committee's own words, strictly from documents published BEFORE the
# meeting (text/README.md, "The leak this exists to avoid"). Exactly the six
# columns text/README.md measured as surviving the persistence control; the
# lexicon tone columns are deliberately absent -- they are mostly last_dir.
TEXT_COLUMNS = ["sep_dot_y0_chg", "sep_dot_y1_chg", "sep_dot_minus_mid",
                "stmt_risk_tilt_chg", "stmt_diff_changed_frac", "stmt_dissent_net"]

# Shocks: the slow macro panel reads a crisis a month late. These are the
# fast-moving versions -- short windows against long ones -- plus a count of
# how many independent stress gauges are tripped at once.
SHOCK_COLUMNS = ["vix_spike", "equity_1m", "credit_jump_1m", "nfci_chg_4w",
                 "oil_1m", "epu_spike", "news_shock", "shock_count"]

COLUMNS = (_CORE_COLUMNS
           + (FUTURES_COLUMNS if FUTURES else [])
           + (NEWS_COLUMNS if NEWS else [])
           + (TEXT_COLUMNS if TEXT else [])
           + (SHOCK_COLUMNS if SHOCK else [])
           # meeting context stays last
           + ["is_sep", "days_since_prev"])


def load_series(refresh: bool = False, verbose: bool = False) -> dict:
    out = {}
    for sid, vintage in SERIES.items():
        out[sid] = fred.series(sid, vintage=vintage, refresh=refresh)
        if verbose:
            print("  %-16s %5d obs  %s -> %s"
                  % (sid, len(out[sid]), out[sid][0].date, out[sid][-1].date))
    if NEWS or SHOCK:
        # Underscored keys: not FRED ids, and never confused with one.
        out["_NEWS"] = news_source.sentiment(refresh=refresh)
        out["_EPU"] = news_source.epu(refresh=refresh)
        if verbose:
            for k in ("_NEWS", "_EPU"):
                print("  %-16s %5d obs  %s -> %s"
                      % (k, len(out[k]), out[k][0].date, out[k][-1].date))
    return out


# -- the text block's corpus, loaded once per process -------------------------

_TEXT_CTX: dict | None = None


def _text_ctx() -> dict:
    """Statements, SEPs and the decision record, loaded lazily.

    Lazily because derive reads data/decisions.json, which matrix.build writes
    immediately before its first build_row call -- loading at import time would
    read the previous build's record.
    """
    global _TEXT_CTX
    if _TEXT_CTX is None:
        import derive
        import sep as sep_mod
        import statements as st
        _TEXT_CTX = {"derive": derive, "corpus": st.load(), "seps": sep_mod.load(),
                     "deltas": derive._delta_by_date(), "path": derive._target_by_date()}
    return _TEXT_CTX


def refresh_text(verbose: bool = False) -> None:
    """Re-harvest the Board's document index and pull any new statement / SEP.

    Archived documents are cached forever; only the index pages are refetched,
    so after a meeting this is a handful of requests.
    """
    global _TEXT_CTX
    import index as ix
    import sep as sep_mod
    import statements as st
    ix.refresh(verbose=verbose)
    st._CACHE = None
    sep_mod._CACHE = None
    st.load(verbose=verbose)
    sep_mod.load(verbose=verbose)
    _TEXT_CTX = None


# -- small helpers ----------------------------------------------------------


def _sub(a, b):
    return None if a is None or b is None else a - b


def _last(obs, when):
    o = fred.latest_as_of(obs, when)
    return o.value if o else None


def _mean_last(obs, when, n):
    vis = fred.as_of(obs, when)
    if len(vis) < n:
        return None
    return sum(o.value for o in vis[-n:]) / n


def _taylor_r_star(data, when) -> float:
    """A slow-moving neutral-real-rate proxy: the 10-year trailing average of the
    ex-post real funds rate, computed only from data visible at `when`.

    Laubach-Williams would be better but is not on FRED and is itself revised.
    A decade of realised real funds is regime-aware, never leaks, and moves the
    way r* is believed to have moved -- ~3% in the 1990s, ~0.5% in the 2010s.
    """
    ff, cpi = data["DFF"], data["CPIAUCSL"]
    nf, nc = ff.upto(when), cpi.upto(when)
    if nf < 500 or nc < 24:
        return 2.0
    cut = fred.shift_days(when, -3650)
    i0 = ff.upto(cut)
    if nf <= i0:
        return 2.0
    nominal = sum(ff[i].value for i in range(i0, nf)) / (nf - i0)
    # average inflation over the same decade, from the monthly index
    j0 = cpi.upto(cut)
    if not j0:
        return 2.0
    infl = ((cpi[nc - 1].value / cpi[j0 - 1].value) ** (1 / 10.0) - 1) * 100
    return max(-1.0, min(4.0, nominal - infl))


def _sahm_gap(unrate, when) -> float | None:
    """Sahm gap: 3-month average unemployment minus its trailing 12-month low.

    Crosses 0.50 at the start of every post-1960 recession. It was the explicit
    centre of the argument over whether the Fed was late in 2024, which is why
    it earns a column of its own rather than being left implicit in unrate_chg.
    """
    vis = fred.as_of(unrate, when)
    if len(vis) < 15:
        return None
    ma = [sum(o.value for o in vis[i - 2:i + 1]) / 3 for i in range(2, len(vis))]
    if len(ma) < 12:
        return None
    return ma[-1] - min(ma[-12:])


# -- the feature vector -----------------------------------------------------


def build_row(data: dict, meeting: dict, path: list, prev_meeting: dict | None,
              asof: str | None = None) -> dict:
    """One meeting -> one feature dict, as of the evening before the meeting opens.

    `asof` moves that reading date earlier, and nothing else: the row is what the
    engine would have seen `asof`, still describing the same meeting. horizon.py
    uses it to score the model N days out, which is how a live forecast weeks
    ahead of a meeting is actually made.
    """
    when = asof or fred.shift_days(meeting["start"], -1)
    f: dict[str, float | None] = {}

    # ---- policy state ----
    level = meeting.get("target_before")
    if level is None:
        level = _last(path, when)
    f["level"] = level
    f["zlb"] = 1.0 if (level is not None and level <= 0.30) else 0.0

    core_pce_yoy = fred.yoy(data["PCEPILFE"], when)
    f["core_pce_yoy"] = core_pce_yoy
    f["real_rate"] = _sub(level, core_pce_yoy)

    # Balanced-approach Taylor rule: r* + pi + 0.5(pi - 2) + 2.0(u* - u).
    # Positive taylor_gap = the rule prescribes a tighter stance than we hold.
    unrate = _last(data["UNRATE"], when)
    nrou = _last(data["NROU"], when) or 4.5
    if core_pce_yoy is not None and unrate is not None and level is not None:
        prescribed = (_taylor_r_star(data, when) + core_pce_yoy
                      + 0.5 * (core_pce_yoy - 2.0) + 2.0 * (nrou - unrate))
        f["taylor_gap"] = prescribed - level
    else:
        f["taylor_gap"] = None

    prior_12m = _last(path, fred.shift_days(when, -365))
    f["chg_12m"] = _sub(level, prior_12m)

    # Inertia: how long since the last move, which way it went, and how many
    # consecutive moves that way. The Fed hikes in runs and cuts in runs.
    f["months_since_move"], f["last_dir"], f["run_length"] = _move_history(path, when)

    # ---- inflation ----
    f["core_pce_gap"] = None if core_pce_yoy is None else core_pce_yoy - 2.0
    f["core_pce_3m"] = fred.annualized(data["PCEPILFE"], when, 3)
    f["core_pce_accel"] = _sub(f["core_pce_3m"], core_pce_yoy)
    f["core_cpi_yoy"] = fred.yoy(data["CPILFESL"], when)
    f["cpi_yoy"] = fred.yoy(data["CPIAUCSL"], when)
    f["breakeven_5y"] = fred.avg_window(data["T5YIE"], when, 30)
    f["real_10y"] = fred.avg_window(data["DFII10"], when, 30)

    # ---- labour ----
    f["unrate"] = unrate
    f["unrate_gap"] = _sub(unrate, nrou)
    f["unrate_chg_6m"] = _sub(unrate, fred.value_n_back(data["UNRATE"], when, 6))
    f["sahm_gap"] = _sahm_gap(data["UNRATE"], when)

    pay = fred.as_of(data["PAYEMS"], when)
    if len(pay) >= 7:
        f["payrolls_3m"] = (pay[-1].value - pay[-4].value) / 3.0     # thousands/month
        prev3 = (pay[-4].value - pay[-7].value) / 3.0
        f["payrolls_chg"] = f["payrolls_3m"] - prev3
    else:
        f["payrolls_3m"] = f["payrolls_chg"] = None

    claims = _mean_last(data["ICSA"], when, 4)
    claims_y = fred.as_of(data["ICSA"], when)
    if claims is not None and len(claims_y) >= 56:
        base = sum(o.value for o in claims_y[-56:-52]) / 4
        f["claims_yoy"] = (claims / base - 1) * 100 if base else None
    else:
        f["claims_yoy"] = None

    openings, unemployed = _last(data["JTSJOL"], when), _last(data["UNEMPLOY"], when)
    f["vacancy_ratio"] = (openings / unemployed) if openings and unemployed else None
    f["partic_chg"] = _sub(_last(data["CIVPART"], when),
                           fred.value_n_back(data["CIVPART"], when, 12))

    # ---- growth ----
    f["gdp_qoq"] = _last(data["A191RL1Q225SBEA"], when)
    f["indpro_yoy"] = fred.yoy(data["INDPRO"], when)
    f["retail_yoy"] = fred.yoy(data["RRSFS"], when)
    f["sentiment"] = _last(data["UMCSENT"], when)
    f["sentiment_chg"] = _sub(f["sentiment"], fred.value_n_back(data["UMCSENT"], when, 6))

    # ---- housing ----
    f["starts_yoy"] = fred.yoy(data["HOUST"], when)
    f["starts_3m"] = fred.annualized(data["HOUST"], when, 3)
    f["permits_yoy"] = fred.yoy(data["PERMIT"], when)
    # Permits lead starts by roughly a quarter; the spread between them is an
    # early read on whether builders are still committing.
    f["permits_lead"] = _sub(f["permits_yoy"], f["starts_yoy"])
    f["newsales_yoy"] = fred.yoy(data["HSN1F"], when)
    f["months_supply"] = _last(data["MSACSR"], when)
    f["hpi_yoy"] = fred.yoy(data["CSUSHPINSA"], when)
    mort = fred.avg_window(data["MORTGAGE30US"], when, 30)
    f["mortgage_spread"] = _sub(mort, fred.avg_window(data["DGS10"], when, 30))
    f["mortgage_chg_6m"] = _sub(mort, fred.avg_window(
        data["MORTGAGE30US"], fred.shift_days(when, -182), 30))

    # ---- markets / what is already priced ----
    eff = fred.avg_window(data["DFF"], when, 10)
    f["mkt_3m"] = _sub(fred.avg_window(data["DGS3MO"], when, 10), eff)
    f["mkt_1y"] = _sub(fred.avg_window(data["DGS1"], when, 10), eff)
    f["mkt_2y"] = _sub(fred.avg_window(data["DGS2"], when, 10), eff)
    f["slope_10y2y"] = fred.avg_window(data["T10Y2Y"], when, 10)
    f["slope_10y3m"] = fred.avg_window(data["T10Y3M"], when, 10)
    prev_date = prev_meeting["date"] if prev_meeting else fred.shift_days(when, -45)
    f["d_2y_intermeeting"] = _sub(fred.avg_window(data["DGS2"], when, 5),
                                  fred.avg_window(data["DGS2"], prev_date, 5))
    f["credit_spread"] = fred.avg_window(data["BAA10Y"], when, 10)
    f["credit_chg"] = _sub(f["credit_spread"],
                           fred.avg_window(data["BAA10Y"], fred.shift_days(when, -90), 10))
    f["vix"] = fred.avg_window(data["VIXCLS"], when, 10)
    f["nfci"] = _last(data["NFCI"], when)

    # Equity block via the panel index -- the "Fed put". A 3-month drawdown is
    # not in the dual mandate, but it moves financial conditions, and financial
    # conditions are what the Committee says it targets.
    eq = data["NASDAQCOM"]
    hi = eq.upto(when)
    if hi:
        now = eq[hi - 1].value
        i90 = eq.upto(fred.shift_days(when, -90))
        f["equity_3m"] = (now / eq[i90 - 1].value - 1) * 100 if i90 else None
        i365 = eq.upto(fred.shift_days(when, -365))
        peak = max((eq[i].value for i in range(i365, hi)), default=None)
        f["equity_drawdown"] = (now / peak - 1) * 100 if peak else None
    else:
        f["equity_3m"] = f["equity_drawdown"] = None
    f["oil_yoy"] = _pct_change_days(data["DCOILWTICO"], when, 365)

    # ---- market-implied policy expectations (opt-in) ----
    # Real futures/options data where a free source reaches, the same
    # DGS3MO-EFFR spread before that. futures.py owns the splice and stamps
    # every real value one day after the settlements it is computed from, so a
    # meeting can never see the row carrying its own decision.
    if FUTURES:
        # A policy change takes effect the day AFTER the announcement -- checked
        # against DFEDTARU, which moves on date+1 at every meeting tested. The
        # FedWatch weighting needs that date, not the announcement date, because
        # what the contract prices is how many days of the month carry the new
        # rate.
        f["ff_exp_move_bp"], _src = ff_source.expected_move_bp(
            when, fred.shift_days(meeting["date"], 1))
        f["ff_p_cut"], f["ff_p_hike"] = ff_source.move_probability(when)

    # ---- news tone + news-measured uncertainty ----
    if NEWS:
        ns, ep = data["_NEWS"], data["_EPU"]
        f["news_sent"] = fred.avg_window(ns, when, 30)
        f["news_sent_chg"] = _sub(f["news_sent"],
                                  fred.avg_window(ns, fred.shift_days(when, -90), 30))
        # Since the last meeting: what the news did while the Committee was away.
        f["news_sent_intermeeting"] = _sub(fred.avg_window(ns, when, 14),
                                           fred.avg_window(ns, prev_date, 14))
        f["epu"] = _log(fred.avg_window(ep, when, 30))
        f["epu_chg"] = _sub(f["epu"], _log(fred.avg_window(ep, fred.shift_days(when, -90), 30)))

    # ---- the Committee's own words, published strictly before the meeting ----
    if TEXT:
        ctx = _text_ctx()
        t = ctx["derive"].row(meeting["date"], corpus=ctx["corpus"], seps=ctx["seps"],
                              deltas=ctx["deltas"], path=ctx["path"])
        for c in TEXT_COLUMNS:
            v = t.get(c)
            f[c] = float(v) if v is not None else None

    # ---- shocks: short windows against long ones ----
    if SHOCK:
        vix5, vix90 = fred.avg_window(data["VIXCLS"], when, 5), fred.avg_window(data["VIXCLS"], when, 90)
        f["vix_spike"] = (vix5 / vix90) if vix5 and vix90 else None
        f["equity_1m"] = _ret_days(data["NASDAQCOM"], when, 30)
        baa = data["BAA10Y"]
        f["credit_jump_1m"] = _sub(fred.avg_window(baa, when, 5),
                                   fred.avg_window(baa, fred.shift_days(when, -30), 5))
        f["nfci_chg_4w"] = _sub(_last(data["NFCI"], when),
                                fred.value_n_back(data["NFCI"], when, 4))
        f["oil_1m"] = _ret_days(data["DCOILWTICO"], when, 30)
        ep = data["_EPU"]
        e7, e365 = fred.avg_window(ep, when, 7), fred.avg_window(ep, when, 365)
        f["epu_spike"] = _log(e7 / e365) if e7 and e365 else None
        ns = data["_NEWS"]
        f["news_shock"] = _sub(fred.avg_window(ns, when, 7), fred.avg_window(ns, when, 90))
        f["shock_count"] = _shock_count(f)

    # ---- meeting context ----
    f["is_sep"] = 1.0 if meeting.get("sep") else 0.0
    f["days_since_prev"] = float(
        (_d(meeting["date"]) - _d(prev_meeting["date"])).days) if prev_meeting else 45.0

    return f


def _d(iso: str):
    from datetime import date
    return date.fromisoformat(iso)


def _log(v):
    import math
    return math.log(v) if v is not None and v > 0 else None


def _ret_days(obs, when, days):
    """Percent change from the last print `days` ago to the last print now.

    Point to point, not averaged: a shock is the thing an average smooths away.
    """
    a = fred.latest_as_of(obs, when)
    b = fred.latest_as_of(obs, fred.shift_days(when, -days))
    if a is None or b is None or b.value <= 0:
        return None
    return (a.value / b.value - 1) * 100


# Trip-wires for shock_count. Each is a level a desk would call a shock, fixed
# in advance rather than fitted -- fitting thresholds on 36 years that contain
# five genuine shocks would be fitting the shocks. The news threshold is ~2.5 sd
# (sd 0.081) of the weekly 7d-vs-90d gap over 1985-2026 -- its 1st percentile,
# measured when the block was added.
SHOCK_TRIPS = {
    "vix_spike": lambda v: v >= 1.5,        # 5-day VIX 50% above its 90-day mean
    "equity_1m": lambda v: v <= -10.0,      # a 10% monthly equity drawdown
    "credit_jump_1m": lambda v: v >= 0.40,  # Baa spread +40bp in a month
    "nfci_chg_4w": lambda v: v >= 0.30,     # conditions tighten 0.3 sd in 4 weeks
    "oil_1m": lambda v: abs(v) >= 25.0,     # oil +/-25% in a month
    "epu_spike": lambda v: v >= 0.69,       # uncertainty double its 1-year mean
    "news_shock": lambda v: v <= -0.20,     # news tone collapse
}


def _shock_count(f: dict) -> float:
    return float(sum(1 for k, trip in SHOCK_TRIPS.items()
                     if f.get(k) is not None and trip(f[k])))


def _pct_change_days(obs, when, days):
    now = fred.avg_window(obs, when, 15)
    then = fred.avg_window(obs, fred.shift_days(when, -days), 15)
    if now is None or then is None or then <= 0:
        return None
    return (now / then - 1) * 100


def _move_history(path, when):
    """(months since the last target change, its direction, length of the run)."""
    vis = fred.as_of(path, when)
    if len(vis) < 2:
        return None, 0.0, 0.0
    moves = []                       # (date, signed change) newest last
    prev = vis[0].value
    for o in vis[1:]:
        if abs(o.value - prev) > 1e-9:
            moves.append((o.date, o.value - prev))
            prev = o.value
    if not moves:
        return None, 0.0, 0.0
    last_date, last_chg = moves[-1]
    months = (_d(when) - _d(last_date)).days / 30.44
    direction = 1.0 if last_chg > 0 else -1.0
    run = 0
    for _, chg in reversed(moves):
        if (chg > 0) == (last_chg > 0):
            run += 1
        else:
            break
    return months, direction, float(run)


def vector(f: dict) -> list:
    """Feature dict -> list in fixed COLUMNS order (None preserved)."""
    return [f.get(c) for c in COLUMNS]
