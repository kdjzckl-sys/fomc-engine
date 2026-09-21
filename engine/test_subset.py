"""The move subset, its baselines, and the futures baseline under them.

    python test_subset.py

Why this file exists. The single number this project is asked for first -- how
the model does on the meetings where the Fed actually moved -- was being derived
in three places: backtest.py published the model's own figure, the dashboard
rederived the whole table in JavaScript off the `preds` array, and a downstream
consumer derived it a third time in Python. All three agreed at 65.75% / 91.78%
by luck, and stopped agreeing the first time the artifact was regenerated
mid-session. backtest.py is the one derivation now. These tests pin it.

Three things are checked, in order of what would hurt most if it broke:

  1. The arithmetic, on a fixture shared byte-for-byte with
     `web/lib/fedSubset.test.mjs`. If the two drift, the dashboard and the CLI
     start quoting different numbers off the same file, which is the failure
     this whole change exists to remove.
  2. The structure: recall and the false-alarm rate that bought it live in the
     same record, and no published row can carry one without the other. 91.8%
     read without its 45.6% is how a trigger-happy yield spread gets mistaken
     for a better forecaster than the model.
  3. The futures baseline's point-in-time discipline and its coverage: a quote
     must be invisible to a meeting that opened before it printed, and the 37
     meetings the strip cannot price must read as uncovered rather than being
     silently graded against the Treasury proxy under a "futures" heading.

No network beyond the cached artifacts the rest of the suite uses.
"""
import json
import sys
from pathlib import Path

import backtest as B
import fred
import futures

HERE = Path(__file__).resolve().parent
FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def near(a, b, tol=1e-9):
    return a is not None and abs(a - b) < tol


def row(rows, key):
    return next((r for r in rows if r["key"] == key), None)


# The fixture, row for row identical to `web/lib/fedSubset.test.mjs`. Three
# moves and two holds, chosen so every caller lands on a different score:
# model 1/3, repeat-last 2/3, curve proxy 3/3, always-hold 0 by construction.
FIXTURE = [
    {"actual": 1, "pred": 0, "prev_label": 1, "mkt_1y": 0.9,
     "ff_priced_bp": 20.0, "ff_source": "futures"},
    {"actual": -1, "pred": -1, "prev_label": -1, "mkt_1y": -0.9,
     "ff_priced_bp": -20.0, "ff_source": "futures"},
    {"actual": 1, "pred": -1, "prev_label": 0, "mkt_1y": 0.9,
     # The strip did not reach this meeting. It must drop out of the futures
     # denominator entirely rather than score as a missed call.
     "ff_priced_bp": 31.0, "ff_source": "proxy"},
    {"actual": 0, "pred": 0, "prev_label": 0, "mkt_1y": 0.0,
     "ff_priced_bp": 2.0, "ff_source": "futures"},
    {"actual": 0, "pred": 0, "prev_label": 0, "mkt_1y": 0.0,
     "ff_priced_bp": 18.0, "ff_source": "futures"},
]


def test_arithmetic():
    print("\nthe arithmetic (fixture shared with web/lib/fedSubset.test.mjs)")
    rows = B.subset_rows(FIXTURE)
    check("holds never enter a move figure",
          all(r["n_moves"] <= 3 for r in rows))
    check("model calls 1 of 3 moves", near(row(rows, "model")["move_direction"], 1 / 3))
    check("repeat-last calls 2 of 3 moves",
          near(row(rows, "repeat_last")["move_direction"], 2 / 3))
    check("the curve proxy calls 3 of 3 moves",
          near(row(rows, "market_proxy")["move_direction"], 1.0))
    check("always-hold scores exactly zero on moves, and is still reported",
          near(row(rows, "always_hold")["move_direction"], 0.0))
    check("always-hold false-alarms on nothing, by construction",
          near(row(rows, "always_hold")["hold_false_alarm"], 0.0))

    # A 50bp cut and a 25bp cut are both "the Fed eased": the call is on sign,
    # not on class equality.
    big = B.subset_rows([{"actual": -2, "pred": -1, "prev_label": -2,
                          "mkt_1y": -0.9, "ff_priced_bp": -40.0,
                          "ff_source": "futures"}])
    check("a 50bp move counts as one directional move",
          near(row(big, "model")["move_direction"], 1.0)
          and near(row(big, "market_futures")["move_direction"], 1.0))

    # The proxy's +-15bp dead band is load-bearing: without it a +10bp spread
    # would score as a correct hike call and the proxy would read better than it
    # is -- the direction of error that flatters us.
    band = B.subset_rows([{"actual": 1, "pred": 1, "prev_label": 0,
                           "mkt_1y": 0.10, "ff_priced_bp": 5.0,
                           "ff_source": "futures"}])
    check("the proxy dead band is +-0.15, not sign(mkt_1y)",
          near(row(band, "market_proxy")["move_direction"], 0.0))
    check("the futures band is +-12.5bp, not sign(bp)",
          near(row(band, "market_futures")["move_direction"], 0.0))
    check("the futures band is half a 25bp increment", B.FUTURES_BAND_BP == 12.5)


def test_futures_coverage():
    print("\nfutures coverage (uncovered is null, never the proxy)")
    rows = B.subset_rows(FIXTURE)
    ff = row(rows, "market_futures")
    check("a proxied meeting leaves the futures denominator",
          ff["n"] == 4 and ff["n_moves"] == 2 and ff["n_holds"] == 2,
          "n=%d moves=%d holds=%d" % (ff["n"], ff["n_moves"], ff["n_holds"]))
    check("the covered moves are both called", near(ff["move_direction"], 1.0))
    check("one of two covered holds false-alarms at 18bp",
          near(ff["hold_false_alarm"], 0.5))
    check("a null price is uncovered, not a wrong call",
          row(B.subset_rows([{"actual": 1, "pred": 1, "prev_label": 0,
                              "mkt_1y": 0.9, "ff_priced_bp": None,
                              "ff_source": "futures"}]),
              "market_futures")["n"] == 0)
    check("preds with no futures fields at all report zero coverage, not zero accuracy",
          row(B.subset_rows([{"actual": 1, "pred": 1, "prev_label": 0, "mkt_1y": 0.9}]),
              "market_futures")["n"] == 0)


def _scoreable(preds):
    """The fixture plus the fields `score()` needs for the headline metrics.

    `subset_rows` only ever reads the call fields, which is why the fixture
    can stay byte-identical to the JavaScript one. `score()` also computes log
    loss and the mean error, so it needs a distribution -- supplied here, and
    kept out of the shared fixture so the two suites cannot drift over padding.
    """
    import model as M
    out = []
    for p in preds:
        probs = [0.1] * len(M.LABELS)
        probs[M.LABELS.index(p["pred"])] = 0.6
        out.append(dict(p, probs=probs, exp_bp=25.0 * p["pred"],
                        actual_bp=25.0 * p["actual"], date="2020-01-01",
                        zlb=False))
    return out


def test_structure():
    print("\nstructure (recall and its price are inseparable)")
    r = B.score(_scoreable(FIXTURE), verbose=False)
    ms = r["move_subset"]
    check("the scorecard publishes a move subset", isinstance(ms, dict))
    check("every caller is a row", len(ms["rows"]) == len(B.CALLERS))
    pair = all(("move_direction" in x and "hold_false_alarm" in x
                and "n_moves" in x and "n_holds" in x) for x in ms["rows"])
    check("no row carries recall without its false-alarm rate", pair)
    check("the hold count is published beside the move count",
          ms["n_moves"] == 3 and ms["n_holds"] == 2)
    check("the like-for-like table rescores every caller on the futures rows",
          ms["like_for_like"]["n"] == 4
          and len(ms["like_for_like"]["rows"]) == len(B.CALLERS))
    check("the legacy scalars still agree with the model row",
          near(r["move_accuracy_direction"], row(ms["rows"], "model")["move_direction"])
          and near(r["false_alarm_rate"], row(ms["rows"], "model")["hold_false_alarm"]))


def test_point_in_time():
    print("\npoint-in-time discipline of the futures baseline")
    st = futures.load()
    panels = st["by_month"]
    check("the strip loaded", len(panels) > 100, "%d delivery months" % len(panels))

    # Every quote is stamped with the day it printed, and the as-of lookup can
    # never return one stamped later than the cutoff. Checked on every delivery
    # month, against a cutoff drawn from the middle of that month's own history,
    # with the additional requirement that a LATER quote exists -- otherwise the
    # test would pass on a panel that simply ended early.
    bad, exercised = [], 0
    for ym in sorted(panels)[:400]:
        panel = panels[ym]
        if len(panel) < 4:
            continue
        cutoff = panel[len(panel) // 2].date
        o = fred.latest_as_of(panel, cutoff)
        if o is None or o.date > cutoff:
            bad.append(ym)
            continue
        if panel[-1].date > cutoff:
            exercised += 1
    check("no quote is visible before the day it printed", not bad,
          "leaked: %s" % bad[:5])
    check("the cutoff filter is actually excluding later quotes",
          exercised > 50, "%d months exercised" % exercised)

    # And end to end: the priced move for a real meeting is computed off the
    # evening before it opened, so it cannot contain the decision. The December
    # 2015 liftoff was fully telegraphed, so the strip should have priced a hike
    # -- a plain sanity check that the arithmetic is wired the right way round.
    bp, src = B._priced_move("2015-12-16", "2015-12-15")
    check("Dec 2015 liftoff was priced as a hike before it happened",
          src == "futures" and bp is not None and bp > B.FUTURES_BAND_BP,
          "%s %s" % (bp, src))


def test_published_artifact():
    print("\nthe published scorecard")
    path = HERE / "models" / "backtest.json"
    if not path.exists():
        print("  SKIP  no backtest.json (run: python cli.py backtest)")
        return
    art = json.loads(path.read_text(encoding="utf-8"))
    if "move_subset" not in art or "preds" not in art:
        print("  SKIP  scorecard predates the move subset (run: python cli.py backtest)")
        return

    ms = art["move_subset"]
    # THE INVARIANT THIS WHOLE CHANGE EXISTS FOR: what is published is exactly
    # what the code derives from the rows beside it. If these ever disagree, the
    # artifact has been hand-edited or half-written, and every consumer reading
    # it is quoting a number nothing produced.
    fresh = B.subset_rows(art["preds"])
    same = True
    for a, b in zip(ms["rows"], fresh):
        for k in ("n", "n_moves", "n_holds"):
            same = same and a[k] == b[k]
        for k in ("all_direction", "move_direction", "hold_false_alarm"):
            same = same and ((a[k] is None and b[k] is None)
                             or near(a[k], b[k], 1e-12))
    check("published rows equal what the preds derive", same)
    check("the futures baseline covers fewer meetings than the others, and says so",
          0 < art["baseline_futures_n"] < art["n"],
          "%s of %s" % (art.get("baseline_futures_n"), art["n"]))
    uncovered = sum(1 for p in art["preds"] if p.get("ff_source") != "futures")
    check("coverage equals the rows the strip actually reached",
          art["baseline_futures_n"] + uncovered == art["n"])
    check("no meeting was graded against the proxy under the futures heading",
          all(B._futures_call(p) is None for p in art["preds"]
              if p.get("ff_source") != "futures"))


def main():
    test_arithmetic()
    test_futures_coverage()
    test_structure()
    test_point_in_time()
    test_published_artifact()
    print("\n%s" % ("ALL PASS" if not FAIL else "FAILED: " + ", ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
