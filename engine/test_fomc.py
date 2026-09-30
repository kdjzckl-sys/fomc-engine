"""Smoke + invariant tests. No network: runs against the cached artifacts.

These are the assertions that would actually catch a regression worth catching --
a leak, a mislabelled decision, a broken ordering constraint -- not coverage
theatre.

    python test_fomc.py
"""
import json
import sys
from pathlib import Path

import calendar_fed
import dataset
import features
import fred
import futures
import matrix as mx
import model as M

HERE = Path(__file__).resolve().parent
FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def main():
    print("\ncalendar")
    cal = calendar_fed.load()
    check("calendar non-empty", len(cal) > 300, "%d" % len(cal))
    sched = [m for m in cal if m["scheduled"]]
    years = {}
    for m in sched:
        years[m["date"][:4]] = years.get(m["date"][:4], 0) + 1
    # The FOMC has met eight times a year since 1981. 2020 is the documented
    # exception (the March 17-18 meeting was cancelled mid-crisis).
    odd = {y: n for y, n in years.items() if n != 8 and "1990" < y < "2027" and y != "2020"}
    check("8 scheduled meetings a year", len(odd) <= 1, str(odd))
    check("future meetings on the calendar", calendar_fed.next_meeting() is not None)

    print("\ndecisions")
    dec = dataset.load()
    check("decisions labelled", len(dec) > 300, "%d" % len(dec))
    check("every delta is a 25bp multiple",
          all(abs(round(r["delta_bp"] / 25) * 25 - r["delta_bp"]) < 0.6 for r in dec))
    check("labels agree with deltas",
          all(r["label"] == dataset.bucket(r["delta_bp"]) for r in dec))
    by_date = {r["date"]: r for r in dec}
    # Four decisions anyone on a desk would know. If the derivation from the
    # target series ever breaks, one of these moves first.
    known = {
        "2020-03-15": -100.0,   # emergency cut to the ZLB
        "2022-06-15": +75.0,    # the first 75 of the 2022 cycle
        "2008-12-16": -75.0,    # cut to 0-0.25, the range begins
        "1994-11-15": +75.0,    # the 1994 shock
    }
    for d, bp in known.items():
        r = by_date.get(d)
        check("known decision %s = %+gbp" % (d, bp),
              r is not None and abs(r["delta_bp"] - bp) < 0.6,
              "got %s" % (r["delta_bp"] if r else "missing"))

    print("\npoint-in-time discipline")
    data = features.load_series()
    # The one that matters: nothing visible at a date may have been published
    # after it. This is the leak the whole design exists to prevent.
    leaks = 0
    for sid, obs in data.items():
        for when in ("1995-06-01", "2008-09-15", "2019-01-01", "2024-06-01"):
            for o in fred.as_of(obs, when):
                if o.pub > when or o.date > when:
                    leaks += 1
                    break
    check("no observation visible before it existed", leaks == 0, "%d leaks" % leaks)
    # NROU carries CBO projections dated years ahead; it is the series that
    # breaks if the date half of the gate is ever dropped.
    nrou = fred.latest_as_of(data["NROU"], "2000-01-01")
    check("projection series cannot leak forward",
          nrou is not None and nrou.date <= "2000-01-01",
          str(nrou))

    print("\nmarket-implied policy expectations (futures.py)")
    # This block runs whether or not the opt-in feature is switched on: the seam
    # has to stay honest while the columns are off, or turning them on later
    # would be turning on something nothing has tested.
    fst = futures.load()
    fmeta = fst["meta"]
    check("futures strip loads back to 1989",
          fmeta["strip_from"] is not None and fmeta["strip_from"] < "1990-01-01"
          and fmeta["n_quote_days"] > 8000,
          "%s, %s days" % (fmeta["strip_from"], fmeta["n_quote_days"]))
    # The splice between two independent vendors is the one place this module
    # could be quietly wrong, so the agreement is MEASURED every run rather than
    # trusted because a comment says so.
    ov = futures.overlap_check()
    check("Yahoo and Nakamura-Steinsson agree on the front contract",
          ov["n"] > 3000 and abs(ov["mean_bp"]) < 1.0 and ov["sd_bp"] < 5.0
          and ov["share_over_5bp"] < 0.02,
          "n=%s mean=%.2fbp sd=%.2fbp >5bp=%.3f"
          % (ov["n"], ov["mean_bp"], ov["sd_bp"], ov["share_over_5bp"]))
    # The strip is read out of a 14-column file by an offset convention
    # (FFE1C is the current month, FFB(k) is k months out). If that offset were
    # wrong by one, every quote would be attributed to the wrong delivery month
    # and nothing else here would notice. It shows up as a discontinuity: each
    # delivery month's own quote series would jump on the day the k=1 columns
    # hand over to the k=0 column. Measure the jump against ordinary day-to-day
    # movement in the same series -- if the mapping is right they are the same
    # size, and they are (median 0bp against a 0bp control).
    hand, ctrl = [], []
    for ym, panel in fst["by_month"].items():
        if not ("1990-01" <= ym <= "2014-12"):
            continue                      # the era with the full 7-contract strip
        prev = None
        for o in panel:
            here = o.date[:7] == ym
            if prev is not None:
                (hand if prev[1] != here else ctrl if here else []).append(
                    abs(o.value - prev[2]) * 100)
            prev = (o.date, here, o.value)
    hand.sort()
    ctrl.sort()
    check("futures strip month-offset mapping is right",
          len(hand) > 200 and hand[len(hand) // 2] <= 1.0
          and hand[int(0.9 * len(hand))] <= 3 * max(ctrl[int(0.9 * len(ctrl))], 1.0),
          "n=%d median %.2fbp p90 %.2fbp vs control p90 %.2fbp"
          % (len(hand), hand[len(hand) // 2], hand[int(0.9 * len(hand))],
             ctrl[int(0.9 * len(ctrl))]))

    # Same rule as every FRED series: a quote can never be visible before the
    # day it printed. A futures price is stamped pub == date (a daily close,
    # like DGS1); the Atlanta Fed report is stamped a day later still.
    fleaks = [ym for ym, panel in fst["by_month"].items()
              if any(o.pub < o.date for o in panel)]
    check("no futures quote is visible before it printed",
          not fleaks, "%d delivery months leak" % len(fleaks))
    check("option-implied probabilities are stamped after their settlement date",
          all(o.pub > o.date for o in fst["p_cut"]))
    # A meeting must never see the tracker row that carries its own decision.
    last_visible = fred.latest_as_of(fst["p_cut"], fmeta["mpt_to"])
    check("a day's own tracker row is invisible at that day's cutoff",
          last_visible is not None and last_visible.date < fmeta["mpt_to"],
          str(last_visible))
    # Before the strip exists the column must fall back to the proxy, and the
    # probabilities must be absent rather than invented from a yield spread.
    pre = "1985-06-01"
    bp_old, src_old = futures.expected_move_bp(pre, "1985-06-12")
    check("falls back to the proxy before the strip starts",
          src_old == "proxy" and bp_old is not None, "%s / %s" % (bp_old, src_old))
    bp_new, src_new = futures.expected_move_bp("2022-06-14", "2022-06-16")
    check("uses real futures where the strip reaches",
          src_new == "futures" and bp_new is not None and bp_new > 40,
          "%s / %s" % (bp_new, src_new))
    pc, ph = futures.move_probability("2015-06-01")
    check("no probability is fabricated before the option record",
          pc is None and ph is None, "%s / %s" % (pc, ph))
    check("futures columns match the flag",
          all((c in features.COLUMNS) == features.FUTURES
              for c in features.FUTURES_COLUMNS),
          "FUTURES=%s" % features.FUTURES)

    print("\nnews / text / shock blocks")
    for flag, cols in ((features.NEWS, features.NEWS_COLUMNS),
                       (features.TEXT, features.TEXT_COLUMNS),
                       (features.SHOCK, features.SHOCK_COLUMNS)):
        check("%s columns match the flag" % cols[0],
              all((c in features.COLUMNS) == flag for c in cols))
    check("published config writes untagged artefacts",
          (features.artifact("matrix") == "matrix.json") == (features.ARTIFACT_TAG == ""))
    if features.NEWS or features.SHOCK:
        import news
        ns = news.sentiment()
        # The lag gate: no sentiment day may be public before PUB_LAG_DAYS after
        # the day it describes, so a meeting can never read its own week's tone.
        check("news sentiment carries its publication lag",
              all(o.pub == fred.shift_days(o.date, news.PUB_LAG_DAYS) for o in ns[-500:]))
        vis = fred.latest_as_of(ns, "2020-03-02")
        check("news visible on 2020-03-02 stops >= %d days earlier" % news.PUB_LAG_DAYS,
              vis is not None and vis.date <= fred.shift_days("2020-03-02", -news.PUB_LAG_DAYS),
              vis.date if vis else "none")
    if features.TEXT:
        # The one leak that would be catastrophic: a meeting reading its own
        # statement. derive is strict; this re-asserts it through the engine path.
        ctx = features._text_ctx()
        for d in ("2008-12-16", "2015-12-16", "2022-03-16", "2024-09-18"):
            s = [x for x in ctx["corpus"] if x.published < d and x.kind == "meeting"]
            own = [x for x in ctx["corpus"] if x.published == d]
            row = ctx["derive"].row(d, corpus=ctx["corpus"], seps=ctx["seps"],
                                    deltas=ctx["deltas"], path=ctx["path"])
            check("text row for %s reads only the prior statement" % d,
                  bool(own) and row.get("stmt_date") == s[-1].published < d,
                  "%s" % row.get("stmt_date"))
    if features.SHOCK:
        f = {"vix_spike": 2.0, "equity_1m": -20.0, "credit_jump_1m": 0.1,
             "nfci_chg_4w": None, "oil_1m": -30.0, "epu_spike": 0.1, "news_shock": -0.3}
        check("shock_count counts tripped gauges and skips missing ones",
              features._shock_count(f) == 4.0, str(features._shock_count(f)))

    print("\nmatrix")
    m = mx.load()
    check("one row per decision", len(m["rows"]) == len(dec))
    check("column count matches features", len(m["columns"]) == len(features.COLUMNS))
    check("every row is the right width",
          all(len(r["x"]) == len(m["columns"]) for r in m["rows"]))
    dense = [c for j, c in enumerate(m["columns"])
             if sum(1 for r in m["rows"] if r["x"][j] is not None) / len(m["rows"]) >= 0.85]
    check("at least 45 dense columns", len(dense) >= 45, "%d" % len(dense))

    print("\nmodel")
    mdl = M.OrderedLogit(3, l2=1.0)
    th = mdl.thetas()
    check("cutpoints strictly increasing", all(th[i] < th[i + 1] for i in range(len(th) - 1)))
    p = mdl.proba([0.0, 0.0, 0.0])
    check("probabilities sum to 1", abs(sum(p) - 1) < 1e-9)
    # A separable toy problem: one feature, latent ordering preserved.
    X = [[-2.0]] * 30 + [[0.0]] * 30 + [[2.0]] * 30
    y = [-1] * 30 + [0] * 30 + [1] * 30
    mdl = M.OrderedLogit(1, l2=0.01).fit(X, y, epochs=400)
    lo, mid, hi = mdl.proba([-2.0]), mdl.proba([0.0]), mdl.proba([2.0])
    check("learns the ordering",
          lo[1] > lo[3] and hi[3] > hi[1] and mid[2] > mid[0],
          "lo=%s hi=%s" % ([round(v, 2) for v in lo], [round(v, 2) for v in hi]))
    check("expected_bp signs correctly",
          M.expected_bp(hi) > 0 > M.expected_bp(lo))

    print("\ntrained artifact")
    # predict.CURRENT tracks the feature-set flag, so a --futures run checks its
    # own model file instead of failing against the default one.
    import predict
    if predict.CURRENT.exists():
        tm, sc, meta = M.load(predict.CURRENT)
        check("saved column order matches features",
              meta["columns"] == features.COLUMNS)
        check("scaler width matches", sc.n == len(features.COLUMNS))
        check("coefficients finite", all(abs(b) < 1e6 for b in tm.beta))
    else:
        print("  SKIP  no trained model at %s (run: python cli.py train)" % predict.CURRENT.name)

    print("\nmarket handoff")
    import horizon
    hz = horizon.load()
    if hz is None:
        print("  SKIP  no horizon record (run: python cli.py horizon)")
    else:
        rows = {r["horizon_days"]: r for r in hz["horizons"]}
        # The 1-day reading IS the published eve-of-meeting read, so it has to
        # reproduce the scorecard's futures column exactly.
        bt_path = predict.HERE / "models" / features.artifact("backtest")
        if 1 in rows and bt_path.exists():
            ff = next(r for r in json.loads(bt_path.read_text(encoding="utf-8"))
                      ["move_subset"]["rows"] if r["key"] == "market_futures")
            check("1-day horizon reproduces the scorecard's futures coverage",
                  rows[1]["n"] == ff["n"], "%s vs %s" % (rows[1]["n"], ff["n"]))
            check("1-day horizon reproduces the scorecard's futures accuracy",
                  abs(rows[1]["market_direction"] - ff["all_direction"]) < 1e-9)
        hd = hz["handoff_days"]
        check("handoff_days is a measured horizon or 0", hd == 0 or hd in rows)
        check("market beats the model at every horizon inside the handoff",
              all(r["market_direction"] > r["model_direction"]
                  for h, r in rows.items() if h <= hd))
        # A meeting three days out is inside any handoff; with the feed gone the
        # headline must still stay on the model rather than show a blank market.
        from datetime import date, timedelta
        soon = date.today() + timedelta(days=3)
        mt = {"date": (soon + timedelta(days=1)).isoformat(), "start": soon.isoformat()}
        orig = predict.market_read
        try:
            predict.market_read = lambda _mt: {"call": None}
            check("missing market feed keeps the headline on the model",
                  predict.market_handoff(mt, (0.1, 0.2, 0.7))["handoff"]
                  ["headline_source"] == "model")
            predict.market_read = lambda _mt: {"call": "hold"}
            check("inside the handoff with a live feed, the market takes the headline",
                  predict.market_handoff(mt, (0.1, 0.2, 0.7))["handoff"]
                  ["headline_source"] == ("market" if hd >= 3 else "model"))
        finally:
            predict.market_read = orig

    print("\n%s" % ("ALL PASS" if not FAIL else "FAILED: " + ", ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
