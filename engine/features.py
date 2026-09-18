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
FUTURES = os.environ.get("FOMC_FUTURES", "1").strip().lower() not in ("0", "false", "no")

if FUTURES:
    import futures as ff_source

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

COLUMNS = (_CORE_COLUMNS
           + (FUTURES_COLUMNS if FUTURES else [])
           # meeting context stays last
           + ["is_sep", "days_since_prev"])


def load_series(refresh: bool = False, verbose: bool = False) -> dict:
    out = {}
    for sid, vintage in SERIES.items():
        out[sid] = fred.series(sid, vintage=vintage, refresh=refresh)
        if verbose:
            print("  %-16s %5d obs  %s -> %s"
                  % (sid, len(out[sid]), out[sid][0].date, out[sid][-1].date))
    return out


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


def build_row(data: dict, meeting: dict, path: list, prev_meeting: dict | None) -> dict:
    """One meeting -> one feature dict. `when` is the day the meeting opens."""
    when = fred.shift_days(meeting["start"], -1)
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

    # ---- meeting context ----
    f["is_sep"] = 1.0 if meeting.get("sep") else 0.0
    f["days_since_prev"] = float(
        (_d(meeting["date"]) - _d(prev_meeting["date"])).days) if prev_meeting else 45.0

    return f


def _d(iso: str):
    from datetime import date
    return date.fromisoformat(iso)


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
