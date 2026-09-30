"""Who should carry the headline, as a function of how far away the meeting is.

The published scorecard (backtest.py) reads everything on the EVENING BEFORE a
meeting opens. On that reading the fed funds futures strip calls direction on
90.8% of the meetings it reaches and the model on 84.7% of the same ones. But the
dashboard's forecast is not made the evening before -- it is made today, weeks
out. The eve-of-meeting number cannot say whether the market still beats the
model four weeks ahead, so this module measures it instead of assuming it.

For each horizon h in HORIZONS_DAYS, every scored meeting is re-read h days
before it opens -- macro data, futures strip, everything, through the same
point-in-time lookups -- and the model's call on that row is set against what
the strip priced that same day. Training is unchanged: the model at meeting i is
fitted on meetings 0..i-1 exactly as backtest.py fits it, and then asked about
meeting i from h days out. That is exactly how a live forecast is produced.

A meeting is only scored at horizon h if it was the NEXT meeting on that day:
if the previous decision had not been announced yet, a forecast "for" this
meeting did not exist, and counting it would score a question nobody asked.

THE HANDOFF RULE, stated before the numbers: the market takes the headline at a
horizon when, on the same meetings, it called direction more often than the
model. `handoff_days` is the longest horizon out to which that holds at every
measured horizon. It is derived here and written to models/horizon.json; the
dashboard and predict.py read it, and nothing anywhere hard-codes it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import backtest as bt
import dataset
import features
import fred
import futures
import matrix as mx
import model as M

HERE = Path(__file__).resolve().parent
OUT = HERE / "models" / features.artifact("horizon")

# Days between the reading and the day the meeting opens. 1 is the published
# eve-of-meeting reading and must reproduce backtest.py; 42 is about as far as a
# meeting stays "the next one" (the Committee meets every six to seven weeks).
HORIZONS_DAYS = (1, 7, 14, 21, 28, 42)


def _dir3(lbl) -> int:
    return -1 if lbl < 0 else (1 if lbl > 0 else 0)


def _share(xs) -> float | None:
    xs = list(xs)
    return (sum(xs) / len(xs)) if xs else None


def run(min_train: int = 60, l2: float = 2.0, half_life: float = 12.0,
        epochs: int = 400, cold_every: int = 24, verbose: bool = True) -> dict:
    data = mx.load()
    cols = data["columns"]
    n_feat = len(cols)
    rows = bt._prep(data["rows"], include_unscheduled=False, include_zlb=True)

    decisions = dataset.load()
    idx = {d["date"]: k for k, d in enumerate(decisions)}
    series = features.load_series()
    path = dataset.policy_path()
    futures.load()

    # per horizon: list of scored records
    scored: dict[int, list[dict]] = {h: [] for h in HORIZONS_DAYS}
    m = M.OrderedLogit(n_feat, l2=l2)
    n_fit = 0
    for i in range(min_train, len(rows)):
        r = rows[i]
        k = idx.get(r["date"])
        if k is None or k == 0:
            continue
        meeting, prev = decisions[k], decisions[k - 1]

        train = rows[:i]
        X_raw = [t["x"] for t in train]
        sc = mx.Scaler(n_feat).fit(X_raw)
        X = [sc.transform(x) for x in X_raw]
        y = [t["label"] for t in train]
        w = M.recency_weights([t["date"] for t in train], half_life)
        cold = (n_fit == 0) or (cold_every and n_fit % cold_every == 0)
        m.l2 = l2
        m.fit(X, y, w, epochs=epochs * (4 if cold else 1), warm=not cold)
        n_fit += 1

        for h in HORIZONS_DAYS:
            asof = fred.shift_days(meeting["start"], -h)
            if asof <= prev["date"]:
                continue            # not yet the next meeting on that day
            mt = dict(meeting)
            if h > 1:
                # the level as it stood on the reading day, not the eve
                mt["target_before"] = None
            f = features.build_row(series, mt, path, prev, asof=asof)
            p = m.proba(sc.transform(features.vector(f)))
            try:
                bp, src = futures.expected_move_bp(
                    asof, fred.shift_days(meeting["date"], 1))
            except Exception:  # noqa: BLE001 - one meeting, not the whole run
                bp, src = None, "none"
            scored[h].append({
                "date": r["date"],
                "actual": _dir3(r["label"]),
                "model": _dir3(M.LABELS[p.index(max(p))]),
                "market": bt._futures_call({"ff_priced_bp": bp, "ff_source": src}),
            })
        if verbose and n_fit % 50 == 0:
            print("  ...%d meetings fitted (through %s)" % (n_fit, r["date"]))

    out_rows = []
    for h in HORIZONS_DAYS:
        recs = scored[h]
        both = [x for x in recs if x["market"] is not None]
        mv = [x for x in both if x["actual"] != 0]
        hd = [x for x in both if x["actual"] == 0]
        out_rows.append({
            "horizon_days": h,
            "n_scored": len(recs),
            "model_direction_all": _share(x["model"] == x["actual"] for x in recs),
            # like for like: only the meetings the strip reached that day
            "n": len(both), "n_moves": len(mv), "n_holds": len(hd),
            "model_direction": _share(x["model"] == x["actual"] for x in both),
            "market_direction": _share(x["market"] == x["actual"] for x in both),
            "model_move_direction": _share(x["model"] == x["actual"] for x in mv),
            "market_move_direction": _share(x["market"] == x["actual"] for x in mv),
            "model_false_alarm": _share(x["model"] != 0 for x in hd),
            "market_false_alarm": _share(x["market"] != 0 for x in hd),
        })

    def market_wins(row) -> bool:
        a, b = row["market_direction"], row["model_direction"]
        return a is not None and b is not None and a > b

    handoff = 0
    for row in out_rows:
        if not market_wins(row):
            break
        handoff = row["horizon_days"]

    res = {
        "built": datetime.now().isoformat(timespec="seconds"),
        "rule": ("market takes the headline at horizons where, on the same "
                 "meetings, it called direction more often than the model; "
                 "handoff_days is the longest horizon out to which that holds "
                 "at every measured horizon"),
        "band_bp": bt.FUTURES_BAND_BP,
        "horizons": out_rows,
        "handoff_days": handoff,
    }
    if verbose:
        print("\n  horizon  n    model   market | on moves: model market |"
              " false alarm: model market")
        for row in out_rows:
            print("  %4dd  %4d   %5.1f%%  %5.1f%% |          %5.1f%% %5.1f%% |"
                  "              %5.1f%% %5.1f%%"
                  % (row["horizon_days"], row["n"],
                     100 * (row["model_direction"] or 0),
                     100 * (row["market_direction"] or 0),
                     100 * (row["model_move_direction"] or 0),
                     100 * (row["market_move_direction"] or 0),
                     100 * (row["model_false_alarm"] or 0),
                     100 * (row["market_false_alarm"] or 0)))
        print("\n  handoff: market carries the headline inside %d days of the"
              " meeting opening" % handoff)
    return res


def load() -> dict | None:
    if not OUT.exists():
        return None
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    r = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(r, indent=1), encoding="utf-8")
    print("  written: %s" % OUT)
