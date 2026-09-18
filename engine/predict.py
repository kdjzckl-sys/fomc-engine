"""Train the production model and predict the next meeting.

Two entry points:

  train()    Fit on the full labelled history and save models/current.json.
             The *reported* quality of that model is the walk-forward number
             from backtest.py, never its own in-sample fit -- an in-sample score
             on 293 meetings and 53 features is decoration.

  forecast() Run the saved model on the next scheduled meeting (or any future
             one), and return the distribution plus an attribution: which
             features pushed the latent policy rate up, which pushed it down,
             and by how much. A probability with no visible drivers is not
             something anyone should trade or argue from.

Attribution is exact here, not approximated: in an ordered logit the latent is
a plain dot product, so each feature's contribution is literally beta_j * z_j.
That is the one real advantage of a linear model over a boosted one and it is
worth more than the couple of points of accuracy it costs.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import calendar_fed
import matrix as mx
import model as M

import features

HERE = Path(__file__).resolve().parent
CURRENT = HERE / "models" / ("current.json" if features.FUTURES
                             else "current.noff.json")


def train(l2: float = 2.0, half_life: float = 12.0, epochs: int = 1200,
          include_unscheduled: bool = False, verbose: bool = True) -> dict:
    data = mx.load()
    cols = data["columns"]
    rows = [r for r in data["rows"] if include_unscheduled or r["scheduled"]]

    X_raw = [r["x"] for r in rows]
    sc = mx.Scaler(len(cols)).fit(X_raw)
    X = [sc.transform(x) for x in X_raw]
    y = [r["label"] for r in rows]
    w = M.recency_weights([r["date"] for r in rows], half_life)

    m = M.OrderedLogit(len(cols), l2=l2)
    m.columns = cols
    m.fit(X, y, w, epochs=epochs, verbose=verbose)

    meta = {
        "trained": datetime.now().isoformat(timespec="seconds"),
        "n_meetings": len(rows),
        "train_from": rows[0]["date"], "train_to": rows[-1]["date"],
        "l2": l2, "half_life_years": half_life, "epochs": epochs,
        "include_unscheduled": include_unscheduled,
        "columns": cols,
    }
    M.save(m, sc, meta, CURRENT)
    if verbose:
        print("trained on %d meetings (%s -> %s) -> %s"
              % (len(rows), meta["train_from"], meta["train_to"], CURRENT))
    return meta


def coefficients(top: int = 20) -> list[tuple]:
    """The fitted reaction function, biggest movers first.

    Sign convention: the latent is 'policy pressure'. A positive coefficient
    means a higher value of that feature pushes toward tightening.
    """
    m, _sc, _meta = M.load(CURRENT)
    pairs = sorted(zip(m.columns, m.beta), key=lambda p: abs(p[1]), reverse=True)
    return pairs[:top]


def forecast(meeting: dict | None = None, refresh: bool = False) -> dict:
    if not CURRENT.exists():
        raise SystemExit("no trained model -- run: python cli.py train")
    m, sc, meta = M.load(CURRENT)

    mt, f = mx.live_row(refresh=refresh, meeting=meeting)
    raw = [f.get(c) for c in meta["columns"]]
    z = sc.transform(raw)
    probs = m.proba(z)
    latent = m.latent(z)

    contrib = sorted(
        [{"feature": c, "value": raw[i], "z": z[i],
          "contribution": m.beta[i] * z[i], "beta": m.beta[i]}
         for i, c in enumerate(meta["columns"])],
        key=lambda d: abs(d["contribution"]), reverse=True)

    p_cut, p_hold, p_hike = M.direction(probs)
    lvl = mt.get("target_before")
    exp = M.expected_bp(probs)

    return {
        "meeting": mt["date"],
        "start": mt.get("start"),
        "sep": mt.get("sep", False),
        "days_away": (date.fromisoformat(mt["date"]) - date.today()).days,
        "current_target_upper": lvl,
        "probs": dict(zip(M.NAMES, probs)),
        "p_cut": p_cut, "p_hold": p_hold, "p_hike": p_hike,
        "expected_bp": exp,
        "implied_target_upper": None if lvl is None else round(lvl + exp / 100, 3),
        "latent": latent,
        "drivers_tightening": [c for c in contrib if c["contribution"] > 0][:8],
        "drivers_easing": [c for c in contrib if c["contribution"] < 0][:8],
        "features": {c: raw[i] for i, c in enumerate(meta["columns"])},
        "model": {"trained": meta["trained"], "n_meetings": meta["n_meetings"],
                  "train_to": meta["train_to"]},
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def path_forecast(n: int = 4, refresh: bool = False) -> list[dict]:
    """Run the model across the next `n` scheduled meetings.

    Important caveat, and it is not a small one: every meeting beyond the first
    is scored on **today's** data. The model has no mechanism to forecast where
    CPI or unemployment will be in six months, so this is "what would the Fed do
    at that meeting if nothing changed", not a rate path. It is useful as a read
    on how much easing/tightening pressure is already in the data, and it is
    wrong to read it as a forecast of the terminal rate.
    """
    out = []
    for mt in calendar_fed.upcoming(n):
        f = forecast(meeting=mt, refresh=refresh)
        f["conditional_on_today"] = True
        out.append(f)
        refresh = False        # only the first pass needs to refresh the cache
    return out


def render(f: dict) -> str:
    L = []
    when = f["meeting"]
    L.append("")
    L.append("  FOMC %s%s   (%d days away)"
             % (when, "  [SEP meeting]" if f["sep"] else "", f["days_away"]))
    L.append("  current target (upper): %.2f%%" % (f["current_target_upper"] or 0))
    L.append("")
    L.append("  %-9s %-8s %s" % ("", "prob", ""))
    for name, p in f["probs"].items():
        bar = "#" * int(round(p * 40))
        L.append("    %-8s %5.1f%%  %s" % (name, 100 * p, bar))
    L.append("")
    L.append("    P(cut)  %5.1f%%    P(hold) %5.1f%%    P(hike) %5.1f%%"
             % (100 * f["p_cut"], 100 * f["p_hold"], 100 * f["p_hike"]))
    L.append("    expected move %+.1f bp  ->  implied target %.2f%%"
             % (f["expected_bp"], f["implied_target_upper"] or 0))
    L.append("")
    L.append("  what is pushing toward TIGHTENING:")
    for c in f["drivers_tightening"][:5]:
        L.append("    %-20s %10s   +%.2f" % (c["feature"], _fmt(c["value"]), c["contribution"]))
    L.append("  what is pushing toward EASING:")
    for c in f["drivers_easing"][:5]:
        L.append("    %-20s %10s   %.2f" % (c["feature"], _fmt(c["value"]), c["contribution"]))
    L.append("")
    L.append("  model trained %s on %d meetings through %s"
             % (f["model"]["trained"][:10], f["model"]["n_meetings"], f["model"]["train_to"]))
    return "\n".join(L)


def _fmt(v):
    if v is None:
        return "n/a"
    return "%.2f" % v if abs(v) < 1000 else "%.0f" % v


if __name__ == "__main__":
    import sys
    if "--train" in sys.argv:
        train()
    print(render(forecast()))
