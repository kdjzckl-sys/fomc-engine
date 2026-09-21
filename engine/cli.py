"""fomc -- the front door.

    python cli.py fetch          refresh the Fed calendar + every FRED series
    python cli.py build          rebuild the labelled history and design matrix
    python cli.py train          fit the production model on the full history
    python cli.py backtest       walk-forward validation (the only honest score);
                                 prints the move subset and the false-alarm rate
                                 it cost, with every baseline beside them
    python cli.py tune           sweep L2 / half-life / feature blocks, walk-forward
    python cli.py predict        the next scheduled meeting
    python cli.py path [--n=4]   the next N meetings, conditional on today's data
    python cli.py coefficients   the fitted reaction function, signed
    python cli.py provenance     where vintage data starts, per series
    python cli.py history [--n=20]  recent meetings and what the Fed did
    python cli.py all            fetch -> build -> train -> backtest -> predict

    --json         machine-readable output (used by the /fed dashboard route)
    --no-futures   drop the fed funds futures block back to the old
                   DGS3MO-EFFR proxy. Writes *.noff artefacts and never
                   touches the published ones, so "do futures actually help"
                   is one command away instead of a claim in a README.

Class C: deterministic, no model calls, no keys beyond FRED.
"""

from __future__ import annotations

import json
import os
import sys


def _flag(name, default=None, cast=str):
    for a in sys.argv[1:]:
        if a == "--" + name:
            return True
        if a.startswith("--" + name + "="):
            return cast(a.split("=", 1)[1])
    return default


def main() -> int:
    cmd = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
           else "predict")
    as_json = bool(_flag("json", False))
    refresh = bool(_flag("refresh", False))

    # --no-futures drops the market-implied block (futures.py) back to the old
    # DGS3MO-EFFR proxy, which is how the claim that futures help stays
    # checkable. It has to be set before features.py is imported anywhere, which
    # is why every import in this file is deliberately lazy. The flagged run
    # writes to its own *.noff matrix / model / scorecard files, so it can never
    # overwrite the published ones.
    if _flag("no-futures", False):
        os.environ["FOMC_FUTURES"] = "0"

    if cmd in ("fetch", "all"):
        import calendar_fed
        import features
        print("fetching FOMC calendar...")
        cal = calendar_fed.refresh(verbose=False)
        print("  %d meetings, %s -> %s" % (len(cal), cal[0]["date"], cal[-1]["date"]))
        print("fetching FRED series...")
        features.load_series(refresh=True, verbose=True)

    if cmd in ("build", "all"):
        import matrix
        matrix.build(refresh=refresh)

    if cmd in ("train", "all"):
        import predict
        predict.train(l2=float(_flag("l2", 2.0)),
                      half_life=float(_flag("half-life", 12.0)),
                      epochs=int(_flag("epochs", 1200)),
                      verbose=not as_json)

    if cmd in ("backtest", "all"):
        import backtest
        r = backtest.run(min_train=int(_flag("min-train", 60)),
                         l2=float(_flag("l2", 2.0)),
                         half_life=float(_flag("half-life", 12.0)),
                         epochs=int(_flag("epochs", 400)),
                         start=_flag("start", None),
                         verbose=not as_json)
        # PERSIST THE SCORECARD HERE, NOT ONLY IN backtest.py's __main__.
        # The write used to live exclusively under `if __name__ == "__main__"`,
        # so `python backtest.py` wrote models/backtest.json and the documented
        # front doors -- `cli.py backtest` and `cli.py all` -- did not. The
        # result was a trained model with no score beside it: lib/fed.ts types
        # the score as `Score | null` and degrades quietly, so /fed rendered a
        # forecast with no accuracy, no baselines and no calibration. A
        # prediction without its baselines is the exact thing this engine's
        # README says it exists not to be.
        backtest.OUT.parent.mkdir(parents=True, exist_ok=True)
        backtest.OUT.write_text(json.dumps(r, indent=1), encoding="utf-8")

        if as_json:
            r.pop("preds", None)
            print(json.dumps(r, indent=1))
        else:
            backtest.confusion(r["preds"])
            backtest.worst(r["preds"])
            print("\n  written: %s" % backtest.OUT)
            # The artifact now carries the move subset itself -- every
            # baseline recomputed on the meetings where the Fed moved, each
            # one paired with the false-alarm rate on the holds that bought
            # it. That number used to be rederived by the dashboard in
            # JavaScript and again by a downstream consumer in Python, and
            # three derivations of one figure drift -- they disagreed the
            # first time this artifact was rebuilt mid-session. There is one
            # derivation now and it is backtest.py.
            ffn = r.get("baseline_futures_n")
            if ffn is not None:
                print("  futures baseline covers %d of %d meetings; the rest"
                      " are null, not quietly proxied" % (ffn, r["n"]))
            if _flag("start", None):
                # The artifact records its own from/to, but a narrowed run
                # silently replacing the full-history scorecard is how a
                # dashboard ends up quoting a number nobody meant to publish.
                print("  NOTE: --start was set, so this scorecard covers a"
                      " NARROWED window. Re-run without --start to restore the"
                      " full-history score.")

    if cmd in ("predict", "all"):
        import predict
        f = predict.forecast(refresh=refresh)
        print(json.dumps(f, indent=1) if as_json else predict.render(f))

    elif cmd == "path":
        import predict
        rows = predict.path_forecast(int(_flag("n", 4)), refresh=refresh)
        if as_json:
            print(json.dumps(rows, indent=1))
        else:
            print("\n  Conditional on TODAY's data -- not a rate path forecast.")
            print("  %-12s %7s %7s %7s  %9s  %s"
                  % ("meeting", "cut", "hold", "hike", "exp move", "implied"))
            for f in rows:
                print("  %-12s %6.1f%% %6.1f%% %6.1f%%  %+7.1fbp  %6.2f%%"
                      % (f["meeting"], 100 * f["p_cut"], 100 * f["p_hold"],
                         100 * f["p_hike"], f["expected_bp"],
                         f["implied_target_upper"] or 0))

    elif cmd == "coefficients":
        import predict
        pairs = predict.coefficients(int(_flag("n", 25)))
        if as_json:
            print(json.dumps([{"feature": c, "beta": b} for c, b in pairs], indent=1))
        else:
            print("\n  fitted reaction function (positive = pushes toward tightening)\n")
            for c, b in pairs:
                bar = ("+" if b > 0 else "-") * min(int(abs(b) * 25), 40)
                print("    %-22s %+7.3f  %s" % (c, b, bar))

    elif cmd == "tune":
        import tune
        tune.sweep(as_json=as_json)

    elif cmd == "provenance":
        import fred
        import features
        import futures
        rows = [fred.provenance(s, v) for s, v in features.SERIES.items()]
        fut = futures.provenance()
        if as_json:
            print(json.dumps({"series": rows, "futures": fut}, indent=1))
        else:
            print("\n  %-18s %6s  %-12s %-12s %6s  %s"
                  % ("series", "n", "first obs", "vintage from", "backfl", "lag"))
            for r in rows:
                print("  %-18s %6d  %-12s %-12s %6s  %s"
                      % (r["series"], r["n"], r.get("first", "-"),
                         r.get("vintage_from") or "(never revised)",
                         r.get("backfilled", 0), r.get("median_lag_days", "-")))
            # The futures splice is the same kind of compromise as the ALFRED
            # one, so it is printed in the same place rather than buried in a
            # README paragraph nobody reads.
            ov = futures.overlap_check()
            print("\n  fed funds futures / market-implied expectations (futures.py)"
                  "   [%s]" % ("ON" if features.FUTURES else "OFF (--no-futures)"))
            print("    ff_exp_move_bp -- CME ZQ strip, FedWatch arithmetic")
            print("      %-14s %s -> %s   (%s)"
                  % ("1989-2015", fut["ns_from"], fut["ns_to"], fut["ns_cite"]))
            print("      %-14s %s -> %s   Yahoo Finance ZQ=F front contract"
                  % ("2000-today", fut["yahoo_from"], fut["yahoo_to"]))
            print("      %-14s %s -> %s   %d quote days"
                  % ("spliced", fut["strip_from"], fut["strip_to"],
                     fut["n_quote_days"]))
            print("      splice agreement over %d overlapping days: mean %+.2fbp,"
                  " sd %.2fbp, %.1f%% differ by >5bp"
                  % (ov["n"], ov["mean_bp"], ov["sd_bp"],
                     100 * ov["share_over_5bp"]))
            print("      %-14s %s" % ("falls back to", fut["proxy"]))
            print("      %-14s before %s, and wherever no contract quote reaches"
                  % ("", fut["strip_from"]))
            print("    ff_p_cut / ff_p_hike -- option-implied, NO proxy exists")
            print("      %-14s %s -> %s   %s"
                  % ("", fut["mpt_from"], fut["mpt_to"], fut["mpt_source"]))
            print("      %-14s null before %s; the imputer handles the gap"
                  % ("", fut["mpt_from"]))
            print("      %-14s %s" % ("licence", fut["mpt_licence"]))
            print("      %s" % fut["mpt_url"])

    elif cmd == "history":
        import dataset
        rows = dataset.load()[-int(_flag("n", 20)):]
        if as_json:
            print(json.dumps(rows, indent=1))
        else:
            print("\n  %-12s %9s  %-8s %6s -> %-6s %s"
                  % ("meeting", "move", "action", "from", "to", ""))
            for r in rows:
                print("  %-12s %+8.1fbp  %-8s %5.2f%% -> %5.2f%% %s"
                      % (r["date"], r["delta_bp"], r["label_name"],
                         r["target_before"], r["target_after"],
                         "" if r["scheduled"] else "UNSCHEDULED"))

    elif cmd not in ("fetch", "build", "train", "backtest", "predict", "all"):
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
