"""Hyperparameter sweep and feature-block ablation -- both walk-forward.

Two questions, and the second is the one that matters.

1. **What regularisation and what memory?** L2 and the recency half-life are the
   two dials that decide whether 53 features on 293 meetings generalises or
   memorises. Swept walk-forward, scored on log loss rather than accuracy,
   because a model that is right 75% of the time with garbage probabilities is
   worse than one that is right 73% with honest ones.

2. **Does the macro data add anything the market had not already priced?**
   This is the whole thesis of the project and it deserves a direct test rather
   than an assertion. Six models, identical protocol:

       market      only the mkt_*/slope_* columns -- what was priced
       macro       inflation + labour + growth + housing, no market columns
       housing     the housing block alone
       policy      policy state and inertia alone
       full        everything
       no-market   everything except the market columns

   If `full` does not beat `market`, the macro layer is decoration and the
   honest answer is to quote the market and stop. Printing that outcome is the
   point of running the test.

Every number here is out-of-sample by construction (backtest.run refits at each
step). Nothing is selected on the test period and then reported as a result --
the sweep output is a decision aid, and whichever setting is chosen gets
re-reported on its own walk-forward run.
"""

from __future__ import annotations

import json
from pathlib import Path

import backtest

import features

HERE = Path(__file__).resolve().parent
# Same rule as the matrix and the scorecard: a --no-futures sweep is a
# different feature set and gets its own artefact rather than overwriting the
# default one.
OUT = HERE / "models" / features.artifact("tuning")

BLOCKS = {
    "market": ["mkt_", "slope_", "d_2y", "credit_", "vix", "nfci", "equity_", "oil_"],
    "macro": ["core_", "cpi_", "breakeven", "real_10y", "unrate", "sahm", "payroll",
              "claims", "vacancy", "partic", "gdp_", "indpro", "retail", "sentiment",
              "starts_", "permits_", "newsales", "months_supply", "hpi_", "mortgage_"],
    "housing": ["starts_", "permits_", "newsales", "months_supply", "hpi_", "mortgage_"],
    "policy": ["level", "real_rate", "taylor_gap", "chg_12m", "months_since_move",
               "last_dir", "run_length", "zlb"],
}
BLOCKS["no-market"] = BLOCKS["policy"] + BLOCKS["macro"] + ["is_sep", "days_since"]
BLOCKS["full"] = [""]      # every column starts with ""


def _run(label, **kw):
    r = backtest.run(verbose=False, **kw)
    r.pop("preds", None)
    r["label"] = label
    return r


def sweep(as_json: bool = False) -> dict:
    grid = []
    print("sweeping regularisation x memory (walk-forward, this takes a few minutes)...")
    for l2 in (0.5, 2.0, 8.0):
        for hl in (6.0, 12.0, 30.0):
            r = _run("l2=%g hl=%g" % (l2, hl), l2=l2, half_life=hl)
            grid.append(r)
            print("  l2=%-4g half-life=%-4g  dir %.1f%%  5cls %.1f%%  logloss %.3f"
                  % (l2, hl, 100 * r["accuracy_direction"],
                     100 * r["accuracy_5class"], r["log_loss"]))

    best = min(grid, key=lambda r: r["log_loss"])
    print("\n  best by log loss: %s" % best["label"])

    print("\nfeature-block ablation (does macro beat what was already priced?)...")
    abl = []
    for name, prefixes in BLOCKS.items():
        r = _run(name, use=prefixes, l2=best["l2"] if "l2" in best else 2.0)
        abl.append(r)
        print("  %-10s n_dir %.1f%%  5cls %.1f%%  logloss %.3f  MAE %.1fbp"
              % (name, 100 * r["accuracy_direction"], 100 * r["accuracy_5class"],
                 r["log_loss"], r["mae_bp"]))

    mk = next(r for r in abl if r["label"] == "market")
    fl = next(r for r in abl if r["label"] == "full")
    lift_dir = 100 * (fl["accuracy_direction"] - mk["accuracy_direction"])
    lift_ll = mk["log_loss"] - fl["log_loss"]
    print("\n  full vs market-only:  direction %+.1f pts, log loss %+.3f" % (lift_dir, lift_ll))
    print("  " + ("macro adds signal beyond what was priced."
                  if lift_ll > 0.005 else
                  "macro adds nothing beyond the market. Quote the market."))

    out = {"grid": grid, "ablation": abl,
           "best": best["label"],
           "lift_direction_pts": lift_dir, "lift_log_loss": lift_ll}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    if as_json:
        print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    sweep()
