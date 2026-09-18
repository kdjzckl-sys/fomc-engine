"""Walk-forward validation. Every number this project reports comes from here.

The rule: to predict meeting i, the model may use meetings 0..i-1 and nothing
else. Refit from scratch at each step -- scaler, imputation medians, cutpoints,
coefficients. That is slower than one fit and a rolling score, and it is the only
version that answers the question anyone cares about, which is "would this have
been right at the time", not "can it describe what happened".

Combined with the vintage data in fred.py, the two together close both leakage
channels: the model never sees a future meeting, and it never sees a revision
that had not printed. Most published Fed-prediction accuracy numbers quietly
violate one or the other and are 10-20 points too high as a result.

Three baselines, because a 75% accuracy headline is meaningless without them:

  always-hold   The Fed holds ~71% of scheduled meetings. Anything that cannot
                beat this is not a model, it is a coin that says "hold".
  market-only   Sign of (1y Treasury - effective funds). What was already priced.
                This is the honest bar: beating it is the entire claim to value.
  persistence   Repeat the last meeting's action. Policy moves in runs, so this
                is stronger than it sounds.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matrix as mx
import model as M

import features

HERE = Path(__file__).resolve().parent
OUT = HERE / "models" / ("backtest.json" if features.FUTURES
                         else "backtest.noff.json")


def _prep(rows, include_unscheduled, include_zlb):
    out = []
    for r in rows:
        if not include_unscheduled and not r["scheduled"]:
            continue
        if not include_zlb and r["zlb"] and r["label"] == 0:
            continue
        out.append(r)
    return out


def run(min_train: int = 60, l2: float = 2.0, half_life: float = 12.0,
        epochs: int = 120, include_unscheduled: bool = False,
        include_zlb: bool = True, start: str | None = None,
        cold_every: int = 24, use: list[str] | None = None,
        verbose: bool = True) -> dict:
    """Refit and predict, one meeting at a time, forward through history.

    `use` restricts the feature set to columns whose name starts with one of the
    given prefixes -- that is how the ablations in tune.py isolate what the macro
    block adds on top of what the market had already priced.
    """
    data = mx.load()
    cols = data["columns"]
    keep = list(range(len(cols)))
    if use:
        keep = [j for j, c in enumerate(cols) if any(c.startswith(u) for u in use)]
        if not keep:
            raise SystemExit("no features matched: " + ",".join(use))
    rows = _prep(data["rows"], include_unscheduled, include_zlb)
    n_feat = len(keep)
    i_mkt1y = cols.index("mkt_1y")

    preds = []
    m = M.OrderedLogit(n_feat, l2=l2)
    for i in range(min_train, len(rows)):
        if start and rows[i]["date"] < start:
            continue
        train = rows[:i]
        X_raw = [[r["x"][j] for j in keep] for r in train]
        sc = mx.Scaler(n_feat).fit(X_raw)
        X = [sc.transform(x) for x in X_raw]
        y = [r["label"] for r in train]
        w = M.recency_weights([r["date"] for r in train], half_life)

        # Cold restart periodically so a warm chain cannot drift into a bad
        # basin and stay there for the rest of the sample.
        cold = (not preds) or (cold_every and len(preds) % cold_every == 0)
        m.l2 = l2
        m.fit(X, y, w, epochs=epochs * (4 if cold else 1), warm=not cold)
        p = m.proba(sc.transform([rows[i]["x"][j] for j in keep]))

        preds.append({
            "date": rows[i]["date"],
            "actual": rows[i]["label"],
            "actual_bp": rows[i]["delta_bp"],
            "probs": p,
            "pred": M.LABELS[p.index(max(p))],
            "exp_bp": M.expected_bp(p),
            "zlb": rows[i]["zlb"],
            # baselines, computed on the same row
            "mkt_1y": rows[i]["x"][i_mkt1y],
            "prev_label": rows[i - 1]["label"],
        })
        if verbose and len(preds) % 25 == 0:
            print("  ...%d meetings scored (through %s)" % (len(preds), rows[i]["date"]))

    return score(preds, verbose=verbose)


def score(preds: list[dict], verbose: bool = True) -> dict:
    n = len(preds)
    if n == 0:
        raise SystemExit("no predictions -- min_train larger than the sample?")

    def dir3(lbl):
        return -1 if lbl < 0 else (1 if lbl > 0 else 0)

    hit = sum(1 for p in preds if p["pred"] == p["actual"])
    hit3 = sum(1 for p in preds if dir3(p["pred"]) == dir3(p["actual"]))
    ll = sum(M.log_loss(p["probs"], p["actual"]) for p in preds) / n
    br = sum(M.brier(p["probs"], p["actual"]) for p in preds) / n
    mae = sum(abs(p["exp_bp"] - p["actual_bp"]) for p in preds) / n

    # baselines
    base_hold = sum(1 for p in preds if p["actual"] == 0) / n
    mkt = sum(1 for p in preds
              if dir3(_mkt_call(p["mkt_1y"])) == dir3(p["actual"])) / n
    persist = sum(1 for p in preds if dir3(p["prev_label"]) == dir3(p["actual"])) / n

    # move-only: how it does when the Fed actually moved (the hard subset)
    moves = [p for p in preds if p["actual"] != 0]
    move_hit = (sum(1 for p in moves if p["pred"] == p["actual"]) / len(moves)) if moves else 0.0
    move_dir = (sum(1 for p in moves if dir3(p["pred"]) == dir3(p["actual"])) / len(moves)) if moves else 0.0
    # and the false-alarm rate: called a move when it held
    holds = [p for p in preds if p["actual"] == 0]
    false_alarm = (sum(1 for p in holds if p["pred"] != 0) / len(holds)) if holds else 0.0

    res = {
        "n": n, "from": preds[0]["date"], "to": preds[-1]["date"],
        "accuracy_5class": hit / n,
        "accuracy_direction": hit3 / n,
        "log_loss": ll, "brier": br, "mae_bp": mae,
        "baseline_always_hold": base_hold,
        "baseline_market_direction": mkt,
        "baseline_persistence": persist,
        "n_moves": len(moves),
        "move_accuracy_5class": move_hit,
        "move_accuracy_direction": move_dir,
        "false_alarm_rate": false_alarm,
        "calibration": calibration(preds),
        "preds": preds,
    }

    if verbose:
        report(res)
    return res


def _mkt_call(mkt_1y):
    """Direction the 1y-vs-funds spread implies. +-15bp deadband: the 1y sits a
    little above funds in normal times (term premium), so a small positive
    spread is not a hike signal."""
    if mkt_1y is None:
        return 0
    if mkt_1y > 0.15:
        return 1
    if mkt_1y < -0.15:
        return -1
    return 0


def calibration(preds: list[dict], bins: int = 5) -> list[dict]:
    """Reliability of P(move). If the model says 30%, does it happen 30% of the time?

    This matters more than accuracy for the way the output is actually used: a
    probability you can size against is worth more than a label you cannot.
    """
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [p for p in preds if lo <= (1 - p["probs"][2]) < hi
               or (b == bins - 1 and (1 - p["probs"][2]) == 1.0)]
        if not sel:
            out.append({"bin": "%.0f-%.0f%%" % (lo * 100, hi * 100), "n": 0,
                        "predicted": None, "actual": None})
            continue
        out.append({
            "bin": "%.0f-%.0f%%" % (lo * 100, hi * 100),
            "n": len(sel),
            "predicted": sum(1 - p["probs"][2] for p in sel) / len(sel),
            "actual": sum(1 for p in sel if p["actual"] != 0) / len(sel),
        })
    return out


def report(r: dict) -> None:
    print("\n" + "=" * 62)
    print("WALK-FORWARD BACKTEST  %s -> %s   (%d meetings)" % (r["from"], r["to"], r["n"]))
    print("=" * 62)
    print("  5-class accuracy        %.1f%%" % (100 * r["accuracy_5class"]))
    print("  direction accuracy      %.1f%%   (cut / hold / hike)" % (100 * r["accuracy_direction"]))
    print("  mean |error|            %.1f bp" % r["mae_bp"])
    print("  log loss                %.3f" % r["log_loss"])
    print("  Brier                   %.3f" % r["brier"])
    print("  -- baselines --")
    print("  always 'hold'           %.1f%%" % (100 * r["baseline_always_hold"]))
    print("  market-implied (1y-FF)  %.1f%%" % (100 * r["baseline_market_direction"]))
    print("  repeat last action      %.1f%%" % (100 * r["baseline_persistence"]))
    print("  -- the hard subset --")
    print("  meetings with a move    %d" % r["n_moves"])
    print("  ...direction right      %.1f%%" % (100 * r["move_accuracy_direction"]))
    print("  ...exact size right     %.1f%%" % (100 * r["move_accuracy_5class"]))
    print("  false alarms on holds   %.1f%%" % (100 * r["false_alarm_rate"]))
    print("  -- calibration of P(move) --")
    for b in r["calibration"]:
        if not b["n"]:
            continue
        print("    said %-8s n=%3d   predicted %4.0f%%   happened %4.0f%%"
              % (b["bin"], b["n"], 100 * b["predicted"], 100 * b["actual"]))


def confusion(preds: list[dict]) -> None:
    print("\n  actual \\ predicted")
    print("           " + "".join("%8s" % n for n in M.NAMES))
    for a in M.LABELS:
        row = Counter(p["pred"] for p in preds if p["actual"] == a)
        print("  %-8s " % M.NAMES[M.LABELS.index(a)]
              + "".join("%8d" % row.get(pl, 0) for pl in M.LABELS))


def worst(preds: list[dict], k: int = 10) -> None:
    print("\n  worst misses (by |expected - actual| bp):")
    for p in sorted(preds, key=lambda p: abs(p["exp_bp"] - p["actual_bp"]), reverse=True)[:k]:
        print("    %s  actual %+6.1fbp   model said %+6.1fbp   P(hold)=%.0f%%"
              % (p["date"], p["actual_bp"], p["exp_bp"], 100 * p["probs"][2]))


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    def opt(name, default, cast=float):
        for a in args:
            if a.startswith("--" + name + "="):
                return cast(a.split("=", 1)[1])
        return default

    r = run(min_train=int(opt("min-train", 60)),
            l2=opt("l2", 2.0),
            half_life=opt("half-life", 12.0),
            epochs=int(opt("epochs", 400)),
            include_unscheduled="--with-unscheduled" in args,
            start=opt("start", None, str))
    confusion(r["preds"])
    worst(r["preds"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(r, indent=1), encoding="utf-8")
    print("\n  written: %s" % OUT)
