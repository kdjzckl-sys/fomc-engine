"""CME watch: the market's priced path, meeting by meeting, FedWatch-style.

predict.market_read answers one question: what does the strip price for the
NEXT meeting. This module answers the one a desk actually opens FedWatch for:
what is priced at every meeting the listed strip reaches, and what that adds up
to as a distribution over target ranges.

It is a different object from `cli.py path`, and the difference matters:

  path   the MODEL, re-run at each meeting on today's data. Not a rate path.
  watch  the MARKET, read off a separate contract for each meeting. It IS a
         rate path -- the one that is priced -- with the usual caveats about
         reading risk-neutral prices as probabilities.

HOW EACH MEETING IS READ

A ZQ contract settles to the average effective funds rate over its delivery
month. For a decision effective on day `e` of a month with `n` days, with the
rate entering the month at `s`:

    R_month = (e-1)/n * s + (n-e+1)/n * r_after
    =>  r_after = (R_month - (e-1)/n * s) * n / (n-e+1)

That is futures.expected_move_bp, applied in sequence. What changes from
meeting to meeting is where `s` comes from:

  * the FIRST meeting starts from the trailing 10-day median of EFFR -- the
    same `r_before` the scored market read uses, so row one of this table is
    the scorecard's number, never a second derivation of it;
  * a later meeting whose previous month has no meeting starts from that
    month's contract (an "anchor" month: one rate all month, so its contract is
    a clean read of the rate going in) -- the CME convention;
  * otherwise it starts from the previous meeting's implied `r_after`.

When fewer than futures.MIN_DAYS_AFTER days of the month carry the new rate,
the division amplifies noise, so the next month's contract is read as `r_after`
outright. If that next month holds a meeting of its own, its average carries
part of that meeting's move too; the row says so (`next_month_has_meeting`)
rather than hiding it.

FROM A RATE TO A DISTRIBUTION

Each meeting's priced move is split across the two 25bp outcomes that bracket
it, linearly (9.5bp -> 38% +25, 62% unchanged). That is FedWatch's two-outcome
assumption, and it is an assumption: the strip carries a mean, not a shape. The
per-meeting splits are then chained -- every path through the earlier meetings
carries forward -- into a distribution over target ranges at each meeting.

No LLM. No key. Class C.
"""

from __future__ import annotations

import calendar
import datetime
import math

import calendar_fed
import dataset
import fred
import futures

STEP_BP = 25.0
# A contract quote older than this many calendar days at read time is flagged.
# A weekend plus a holiday is four; anything past that is a dead feed.
STALE_DAYS = 4
# Paths below this probability are dropped from the tree so 8 meetings of
# splitting cannot grow it without bound. Mass is renormalised after pruning.
PRUNE = 1e-6


def _ym(d: datetime.date) -> str:
    return "%04d-%02d" % (d.year, d.month)


def _symbol(ym: str) -> str:
    return "ZQ%s%s" % (futures.MONTH_CODE[int(ym[5:7]) - 1], ym[2:4])


def split(move_bp: float) -> dict[int, float]:
    """FedWatch's two-outcome split of a priced move into 25bp steps.

    {step_bp: probability}. A move of exactly k*25 is all on k*25; anything in
    between is shared linearly by the two multiples that bracket it.
    """
    x = round(move_bp, 6) / STEP_BP
    lo = math.floor(x)
    w = x - lo
    out = {int(lo * STEP_BP): 1.0 - w}
    if w > 0:
        out[int((lo + 1) * STEP_BP)] = w
    return {k: p for k, p in out.items() if p > 0}


def chain(tree: dict[int, float], step: dict[int, float]) -> dict[int, float]:
    """Carry every path through one more meeting: a discrete convolution."""
    out: dict[int, float] = {}
    for c, p in tree.items():
        for s, q in step.items():
            out[c + s] = out.get(c + s, 0.0) + p * q
    out = {k: v for k, v in out.items() if v >= PRUNE}
    tot = sum(out.values())
    return {k: v / tot for k, v in out.items()}


def _direction(dist: dict[int, float]) -> tuple[float, float, float]:
    return (sum(p for k, p in dist.items() if k < 0),
            sum(p for k, p in dist.items() if k == 0),
            sum(p for k, p in dist.items() if k > 0))


def _ranges(dist: dict[int, float], upper: float) -> list[dict]:
    rows = []
    for k in sorted(dist, reverse=True):
        up = round(upper + k / 100.0, 4)
        rows.append({"lower": round(up - 0.25, 4), "upper": up,
                     "label": "%d-%d" % (round((up - 0.25) * 100), round(up * 100)),
                     "change_bp": k, "p": dist[k]})
    return rows


def read(n: int = 8, when: str | None = None, refresh: bool = False) -> dict:
    """The watch table for the next `n` scheduled meetings, as of `when`."""
    when = when or datetime.date.today().isoformat()
    st = futures.load(refresh=refresh)
    r0 = futures._r_before(st, when)
    pol = fred.latest_as_of(dataset.policy_path(refresh=refresh), when)
    upper = pol.value if pol else None
    meetings = calendar_fed.upcoming(n, after=when)

    out = {
        "as_of": when,
        "current_target_upper": upper,
        "current_range": (None if upper is None else
                          "%d-%d" % (round((upper - 0.25) * 100), round(upper * 100))),
        "effr_start": r0,
        "effr_start_source": "10-day median of DFF (EFFR) -- the scored market read's r_before",
        "method": "CME FedWatch arithmetic on the ZQ strip, two-outcome split, chained",
        "rows": [],
        "unreached": [],
        "note": None,
    }
    if r0 is None or upper is None:
        out["note"] = ("no EFFR or target as of %s -- run: python cli.py fetch" % when)
        return out

    eff_months = {}
    for mt in meetings:
        e = datetime.date.fromisoformat(fred.shift_days(mt["date"], 1))
        eff_months.setdefault(_ym(e), mt["date"])
    today_ym = when[:7]

    tree = {0: 1.0}
    prev_end = r0
    for i, mt in enumerate(meetings):
        e = datetime.date.fromisoformat(fred.shift_days(mt["date"], 1))
        ym = _ym(e)
        n_days = calendar.monthrange(e.year, e.month)[1]
        days_before = e.day - 1
        days_after = n_days - days_before

        # Where the rate enters this meeting's month.
        start, start_src = prev_end, "previous meeting's implied rate"
        if i == 0:
            start, start_src = r0, "EFFR, 10-day median"
        else:
            pm = futures._ym_shift(ym, -1)
            if pm > today_ym and pm not in eff_months:
                rate, q = futures._rate_for_month(st, pm, when)
                if rate is not None:
                    start, start_src = rate, "anchor month %s (%s)" % (pm, _symbol(pm))

        # Where it leaves.
        end, used, quote, method = None, None, None, None
        nm = futures._ym_shift(ym, 1)
        if days_after >= futures.MIN_DAYS_AFTER:
            rate, q = futures._rate_for_month(st, ym, when)
            if rate is not None:
                end = (rate - (days_before / n_days) * start) * n_days / days_after
                used, quote, method = ym, (rate, q), "meeting-month weighting"
        if end is None:
            rate, q = futures._rate_for_month(st, nm, when)
            if rate is not None:
                end = rate
                used, quote, method = nm, (rate, q), "next-month contract, outright"

        if end is None:
            out["unreached"] = [m["date"] for m in meetings[i:]]
            break

        move = 100.0 * (end - start)
        step = split(move)
        tree = chain(tree, step)
        prev_end = end

        q_rate, q_date = quote
        age = (datetime.date.fromisoformat(when)
               - datetime.date.fromisoformat(q_date)).days
        cut, hold, hike = _direction(step)
        ccut, chold, chike = _direction(tree)
        rows = _ranges(tree, upper)
        top = max(rows, key=lambda r: r["p"])
        out["rows"].append({
            "meeting": mt["date"],
            "start": mt.get("start"),
            "sep": bool(mt.get("sep")),
            "effective": e.isoformat(),
            "days_away": (datetime.date.fromisoformat(mt["date"])
                          - datetime.date.fromisoformat(when)).days,
            "contract": _symbol(used),
            "contract_month": used,
            "contract_price": round(100.0 - q_rate, 4),
            "contract_rate": q_rate,
            "quote_date": q_date,
            "stale": age > STALE_DAYS,
            "method": method,
            "next_month_has_meeting": (method.startswith("next-month")
                                       and nm in eff_months and eff_months[nm] != mt["date"]),
            "rate_in": start,
            "rate_in_source": start_src,
            "implied_effr": end,
            "move_bp": move,
            "cum_bp": 100.0 * (end - r0),
            "tree_mean_bp": sum(k * p for k, p in tree.items()),
            "implied_target_upper": round(upper + sum(k * p for k, p in tree.items()) / 100, 4),
            "this_meeting": {"p_cut": cut, "p_hold": hold, "p_hike": hike,
                             "outcomes": {str(k): p for k, p in sorted(step.items())}},
            "vs_today": {"p_lower": ccut, "p_unchanged": chold, "p_higher": chike},
            "ranges": rows,
            "most_likely": top["label"],
        })

    out["options_check"] = options_check(out, when, refresh=refresh)
    return out


def options_check(w: dict, when: str, refresh: bool = False) -> dict | None:
    """The Atlanta Fed's option-implied read beside the futures tree, window by window.

    An independent instrument (SOFR options, not fed funds futures), so where
    the two agree the strip read is corroborated, and where they part it is
    usually the tails: the options can put weight on a cut inside a hiking
    path, and a two-outcome split of the futures mean structurally cannot.
    Each option window is matched to the last meeting decided before it.
    """
    try:
        mpt = futures.mpt_windows(when, refresh=refresh)
    except Exception:  # noqa: BLE001 - a dead upstream drops the check, not the table
        return None
    if not mpt or not w["rows"]:
        return None
    rows = []
    # Past the last meeting the tree reaches, a window could hold a meeting the
    # tree never priced; ~8 weeks is the longest scheduled gap.
    reach = fred.shift_days(w["rows"][-1]["effective"], 56)
    for win in mpt["windows"]:
        before = [r for r in w["rows"] if r["effective"] <= win["window"]]
        if not before or win["window"] > reach:
            continue
        r = before[-1]
        rows.append({
            "window": win["window"], "meeting": r["meeting"],
            "options_p_higher": win["p_hike"], "options_p_lower": win["p_cut"],
            "futures_p_higher": r["vs_today"]["p_higher"],
            "futures_p_lower": r["vs_today"]["p_lower"],
            "gap_higher": (None if win["p_hike"] is None
                           else r["vs_today"]["p_higher"] - win["p_hike"]),
        })
    return {"source": futures.MPT_PAGE, "licence": futures.MPT_LICENCE,
            "report_date": mpt["date"], "rows": rows}


def render(w: dict) -> str:
    L = ["", "  CME watch -- fed funds futures, FedWatch arithmetic, as of %s" % w["as_of"]]
    if w.get("note"):
        L.append("  " + w["note"])
        return "\n".join(L)
    L.append("  current range %s   EFFR in %.3f%% (%s)"
             % (w["current_range"], w["effr_start"], "10-day median"))
    L.append("")
    L.append("  %-11s %-7s %8s %8s %8s   %6s %6s %6s   %-9s %s"
             % ("meeting", "ZQ", "price", "this mtg", "cum", "cut", "hold", "hike",
                "likely", "vs today: lower/unch/higher"))
    for r in w["rows"]:
        t, v = r["this_meeting"], r["vs_today"]
        flag = (" STALE %s" % r["quote_date"] if r["stale"] else "") + \
               (" *" if r["next_month_has_meeting"] else "")
        L.append("  %-11s %-7s %8.4f %+7.1fbp %+7.1fbp   %5.1f%% %5.1f%% %5.1f%%   %-9s %5.1f/%5.1f/%5.1f%s"
                 % (r["meeting"] + ("S" if r["sep"] else " "), r["contract"],
                    r["contract_price"], r["move_bp"], r["cum_bp"],
                    100 * t["p_cut"], 100 * t["p_hold"], 100 * t["p_hike"],
                    r["most_likely"], 100 * v["p_lower"], 100 * v["p_unchanged"],
                    100 * v["p_higher"], flag))
    if w["unreached"]:
        L.append("  not reached by the listed strip: %s" % ", ".join(w["unreached"]))
    L.append("")
    L.append("  target-range probabilities by meeting (%):")
    labels = sorted({x["label"] for r in w["rows"] for x in r["ranges"]},
                    key=lambda s: -int(s.split("-")[1]))
    L.append("  %-11s " % "" + " ".join("%8s" % lb for lb in labels))
    for r in w["rows"]:
        m = {x["label"]: x["p"] for x in r["ranges"]}
        L.append("  %-11s " % r["meeting"] + " ".join(
            "%8s" % ("%.1f" % (100 * m[lb]) if m.get(lb, 0) >= 0.0005 else "")
            for lb in labels))
    oc = w.get("options_check")
    if oc and oc["rows"]:
        L.append("")
        L.append("  cross-check: Atlanta Fed SOFR options, report %s (%%, vs today's range)"
                 % oc["report_date"])
        L.append("  %-11s %-11s %15s %15s" % ("by", "after mtg", "higher fut/opt",
                                              "lower fut/opt"))
        for r in oc["rows"]:
            L.append("  %-11s %-11s %7.1f /%5.1f %7.1f /%5.1f"
                     % (r["window"], r["meeting"], 100 * r["futures_p_higher"],
                        100 * (r["options_p_higher"] or 0), 100 * r["futures_p_lower"],
                        100 * (r["options_p_lower"] or 0)))
    L.append("")
    L.append("  Risk-neutral prices read as probabilities, two-outcome split per meeting.")
    L.append("  S = SEP meeting.  * = read off a next-month contract that holds a meeting of its own.")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    print(render(read(refresh="--refresh" in sys.argv)))
