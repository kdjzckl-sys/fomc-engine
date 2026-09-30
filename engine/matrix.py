"""Assemble the design matrix: one row per meeting, features as of that morning.

Cached to data/matrix.json because building it walks ~30 series across ~360
meetings and that costs a few seconds every time. Rebuild with `cli.py build`.

Imputation and scaling both live here, and both are *expanding-window* by
construction: the statistics used to fill and scale row i are computed from rows
0..i-1 only. Using a full-sample mean would leak the future into the backtest
through the back door -- a subtler version of the same mistake as using revised
data, and one that quietly inflates every reported accuracy number.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import dataset
import features

HERE = Path(__file__).resolve().parent
# Turning the futures block off changes the column set, so that build gets its
# own artefact. Sharing one file would let a --no-futures build silently replace
# the matrix the published scorecard was computed on.
OUT = HERE / "data" / features.artifact("matrix")


def build(refresh: bool = False, verbose: bool = True) -> dict:
    if verbose:
        print("loading series...")
    data = features.load_series(refresh=refresh, verbose=verbose)
    path = dataset.policy_path(refresh=refresh)
    decisions = dataset.build(refresh=refresh)

    rows = []
    prev = None
    for m in decisions:
        f = features.build_row(data, m, path, prev)
        rows.append({
            "date": m["date"],
            "scheduled": m["scheduled"],
            "zlb": m["zlb"],
            "label": m["label"],
            "delta_bp": m["delta_bp"],
            "target_before": m["target_before"],
            "x": features.vector(f),
        })
        prev = m

    out = {
        "built": datetime.now().isoformat(timespec="seconds"),
        "columns": features.COLUMNS,
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out), encoding="utf-8")
    if verbose:
        print("matrix: %d rows x %d features" % (len(rows), len(features.COLUMNS)))
    return out


def load(auto: bool = True) -> dict:
    if not OUT.exists():
        if not auto:
            raise SystemExit("no matrix -- run: python cli.py build")
        return build()
    return json.loads(OUT.read_text(encoding="utf-8"))


def live_row(refresh: bool = False, meeting: dict | None = None) -> tuple[dict, dict]:
    """Feature row for a meeting that has not happened yet.

    `meeting` defaults to the next scheduled one. Returns (meeting, feature dict)
    so callers can show the raw inputs alongside the prediction -- a probability
    with no visible inputs is not worth much.
    """
    import calendar_fed

    meeting = meeting or calendar_fed.next_meeting()
    if meeting is None:
        raise SystemExit("no future meeting on the calendar -- run: cli.py fetch")
    data = features.load_series(refresh=refresh)
    path = dataset.policy_path(refresh=refresh)
    decisions = dataset.load()
    prev = decisions[-1] if decisions else None
    m = dict(meeting)
    m["target_before"] = path[-1].value
    return m, features.build_row(data, m, path, prev)


# -- expanding-window imputation + scaling ----------------------------------


class Scaler:
    """Median-impute then z-score, fitted on a given set of rows.

    Median rather than mean because several columns (VIX, claims, oil) are
    right-skewed enough that a mean fill would bias the neutral value.
    """

    def __init__(self, n_cols: int):
        self.n = n_cols
        self.med = [0.0] * n_cols
        self.mu = [0.0] * n_cols
        self.sd = [1.0] * n_cols

    def fit(self, X: list[list]) -> "Scaler":
        for j in range(self.n):
            vals = sorted(r[j] for r in X if r[j] is not None)
            self.med[j] = vals[len(vals) // 2] if vals else 0.0
            filled = [r[j] if r[j] is not None else self.med[j] for r in X]
            if not filled:
                continue
            mu = sum(filled) / len(filled)
            var = sum((v - mu) ** 2 for v in filled) / max(len(filled) - 1, 1)
            self.mu[j] = mu
            self.sd[j] = var ** 0.5 if var > 1e-12 else 1.0
        return self

    def transform(self, row: list) -> list[float]:
        out = []
        for j in range(self.n):
            v = row[j]
            if v is None:
                v = self.med[j]
            z = (v - self.mu[j]) / self.sd[j]
            # Clip so one crisis print (VIX 80, claims 6m) cannot dominate a
            # gradient step. Genuine extremes still read as extreme at 5 sigma.
            out.append(max(-5.0, min(5.0, z)))
        return out

    def to_dict(self) -> dict:
        return {"n": self.n, "med": self.med, "mu": self.mu, "sd": self.sd}

    @staticmethod
    def from_dict(d: dict) -> "Scaler":
        s = Scaler(d["n"])
        s.med, s.mu, s.sd = d["med"], d["mu"], d["sd"]
        return s


if __name__ == "__main__":
    import sys
    m = build(refresh="--refresh" in sys.argv)
    cols = m["columns"]
    rows = m["rows"]
    print("\ncoverage (share of meetings where the feature is observed):")
    for j, c in enumerate(cols):
        have = sum(1 for r in rows if r["x"][j] is not None)
        flag = "  <-- sparse" if have / len(rows) < 0.75 else ""
        print("  %-20s %5.1f%%%s" % (c, 100 * have / len(rows), flag))
