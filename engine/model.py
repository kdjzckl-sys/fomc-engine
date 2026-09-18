"""Ordered logistic regression (proportional odds), in plain Python.

Why this model and not a forest or a net. The Committee's choice set is *ordered*
-- cut50 < cut25 < hold < hike25 < hike50 -- and a multinomial classifier throws
that away, happily assigning "hike50" and "cut50" adjacent probabilities on
neighbouring inputs. The proportional-odds model puts every meeting on a single
latent policy-pressure axis and slices it with four ordered cutpoints, which is
exactly the shape of the thing being modelled: one underlying "how much tightening
does the situation call for", discretised into 25bp increments.

It also buys two properties that matter more than a point or two of accuracy:

  * **One coefficient per feature, signed.** You can read the fitted model as a
    reaction function -- core PCE pushes the latent up, the Sahm gap pushes it
    down -- and check it against what a desk believes. A gradient-boosted model
    of 293 observations would score better in-sample and tell you nothing.
  * **Calibrated probabilities by construction.** The output is a distribution,
    not a label. "68% hold / 27% cut25" is the useful answer; argmax is not.

With 293 scheduled meetings and 53 features, overfitting is the live risk, so:
L2 on every coefficient (cutpoints unpenalised), gradients on standardised
inputs, and a recency half-life that lets Volcker-era meetings inform the shape
without letting them outvote the last decade. Everything is validated
walk-forward in backtest.py -- no number in this repo comes from in-sample fit.

No dependencies. No LLM. Class C.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = [-2, -1, 0, 1, 2]
NAMES = ["cut50+", "cut25", "hold", "hike25", "hike50+"]
BP = [-62.5, -25.0, 0.0, 25.0, 62.5]
K = len(LABELS)


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class OrderedLogit:
    """P(y <= k) = sigmoid(theta_k - x.beta), theta strictly increasing.

    Cutpoints are stored as (a0, a1..a3) with theta_0 = a0 and
    theta_k = theta_{k-1} + exp(a_k), so the ordering constraint holds for any
    value the optimiser lands on and never needs projecting back.
    """

    def __init__(self, n_features: int, l2: float = 1.0):
        self.p = n_features
        self.l2 = l2
        self.beta = [0.0] * n_features
        self.a = [0.0] + [math.log(1.0)] * (K - 2)   # K-1 cutpoints
        self.columns: list[str] = []

    # -- cutpoints --
    def thetas(self) -> list[float]:
        th = [self.a[0]]
        for j in range(1, K - 1):
            th.append(th[-1] + math.exp(self.a[j]))
        return th

    def latent(self, x: list[float]) -> float:
        return sum(b * v for b, v in zip(self.beta, x))

    def proba(self, x: list[float]) -> list[float]:
        xb = self.latent(x)
        th = self.thetas()
        cum = [sigmoid(t - xb) for t in th] + [1.0]
        out, prev = [], 0.0
        for c in cum:
            out.append(max(c - prev, 1e-12))
            prev = c
        s = sum(out)
        return [v / s for v in out]

    # -- fitting --
    def fit(self, X: list[list[float]], y: list[int], w: list[float] | None = None,
            epochs: int = 600, lr: float = 0.08, verbose: bool = False,
            warm: bool = False) -> "OrderedLogit":
        """Fit by Adam on the penalised negative log-likelihood.

        `warm=True` keeps the current coefficients as the starting point. The
        walk-forward backtest uses it to carry step i-1's solution into step i,
        which cuts the run from ~25 minutes to ~4 with no leakage: step i-1 was
        trained on a strict subset of step i's training data, so nothing from
        the future enters through the initialisation.
        """
        n = len(X)
        if n == 0:
            return self
        w = w or [1.0] * n
        wsum = sum(w)
        yi = [LABELS.index(v) for v in y]

        if not warm:
            # Start the cutpoints at the empirical cumulative log-odds so the
            # optimiser opens from the right base rates instead of from uniform.
            counts = [max(sum(1 for v in yi if v == k), 1) for k in range(K)]
            tot = sum(counts)
            cum, th0 = 0.0, []
            for k in range(K - 1):
                cum += counts[k] / tot
                th0.append(math.log(cum / (1 - cum)))
            self.a[0] = th0[0]
            for j in range(1, K - 1):
                self.a[j] = math.log(max(th0[j] - th0[j - 1], 1e-3))

        # Adam. Plain SGD converges here too but needs ~10x the epochs, and the
        # cutpoint and coefficient gradients live on very different scales.
        mb = [0.0] * self.p; vb = [0.0] * self.p
        ma = [0.0] * (K - 1); va = [0.0] * (K - 1)
        b1, b2, eps = 0.9, 0.999, 1e-8

        for ep in range(1, epochs + 1):
            gb = [0.0] * self.p
            ga = [0.0] * (K - 1)
            nll = 0.0
            th = self.thetas()
            expa = [1.0] + [math.exp(self.a[j]) for j in range(1, K - 1)]

            for i in range(n):
                x, k, wi = X[i], yi[i], w[i]
                xb = self.latent(x)
                s_hi = sigmoid(th[k] - xb) if k < K - 1 else 1.0
                s_lo = sigmoid(th[k - 1] - xb) if k > 0 else 0.0
                p = max(s_hi - s_lo, 1e-12)
                nll -= wi * math.log(p)

                d_hi = s_hi * (1 - s_hi) if k < K - 1 else 0.0
                d_lo = s_lo * (1 - s_lo) if k > 0 else 0.0
                # d(-log p)/d(xb)
                g_xb = wi * (d_hi - d_lo) / p
                for j in range(self.p):
                    gb[j] += g_xb * x[j]

                # d(-log p)/d(theta_k) and /d(theta_{k-1})
                dth = [0.0] * (K - 1)
                if k < K - 1:
                    dth[k] = -wi * d_hi / p
                if k > 0:
                    dth[k - 1] = wi * d_lo / p
                # chain through the reparameterisation
                for j in range(K - 1):
                    if dth[j] == 0.0:
                        continue
                    ga[0] += dth[j]
                    for q in range(1, j + 1):
                        ga[q] += dth[j] * expa[q]

            # L2 on coefficients only -- penalising cutpoints would fight the
            # base rates the model must reproduce.
            for j in range(self.p):
                gb[j] = gb[j] / wsum + 2.0 * self.l2 * self.beta[j] / n
            ga = [g / wsum for g in ga]

            for j in range(self.p):
                mb[j] = b1 * mb[j] + (1 - b1) * gb[j]
                vb[j] = b2 * vb[j] + (1 - b2) * gb[j] * gb[j]
                mh = mb[j] / (1 - b1 ** ep); vh = vb[j] / (1 - b2 ** ep)
                self.beta[j] -= lr * mh / (math.sqrt(vh) + eps)
            for j in range(K - 1):
                ma[j] = b1 * ma[j] + (1 - b1) * ga[j]
                va[j] = b2 * va[j] + (1 - b2) * ga[j] * ga[j]
                mh = ma[j] / (1 - b1 ** ep); vh = va[j] / (1 - b2 ** ep)
                self.a[j] -= lr * mh / (math.sqrt(vh) + eps)

            if verbose and ep % 100 == 0:
                print("    epoch %4d  nll/obs %.4f" % (ep, nll / wsum))
        return self

    # -- serialisation --
    def to_dict(self) -> dict:
        return {"p": self.p, "l2": self.l2, "beta": self.beta, "a": self.a,
                "columns": self.columns}

    @staticmethod
    def from_dict(d: dict) -> "OrderedLogit":
        m = OrderedLogit(d["p"], d["l2"])
        m.beta, m.a = d["beta"], d["a"]
        m.columns = d.get("columns", [])
        return m


# -- scoring helpers --------------------------------------------------------


def expected_bp(probs: list[float]) -> float:
    return sum(p * b for p, b in zip(probs, BP))


def direction(probs: list[float]) -> tuple[float, float, float]:
    """Collapse to (P cut, P hold, P hike) -- the number a desk actually quotes."""
    return probs[0] + probs[1], probs[2], probs[3] + probs[4]


def log_loss(probs: list[float], y: int) -> float:
    return -math.log(max(probs[LABELS.index(y)], 1e-12))


def brier(probs: list[float], y: int) -> float:
    k = LABELS.index(y)
    return sum((p - (1.0 if i == k else 0.0)) ** 2 for i, p in enumerate(probs))


def recency_weights(dates: list[str], half_life_years: float) -> list[float]:
    """Exponential decay toward the most recent meeting in the set.

    The reaction function is not stationary: Greenspan's Fed, the ZLB decade and
    the post-2021 Fed weight the same inflation print differently. A half-life
    keeps old meetings informative about shape without letting 1994 outvote 2024.
    """
    if not dates:
        return []
    last = max(dates)
    ly = int(last[:4]) + int(last[5:7]) / 12.0
    out = []
    for d in dates:
        yy = int(d[:4]) + int(d[5:7]) / 12.0
        out.append(0.5 ** ((ly - yy) / half_life_years))
    return out


def save(model: OrderedLogit, scaler, meta: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"model": model.to_dict(), "scaler": scaler.to_dict(), "meta": meta},
        indent=1), encoding="utf-8")


def load(path: Path):
    import matrix
    d = json.loads(path.read_text(encoding="utf-8"))
    return (OrderedLogit.from_dict(d["model"]),
            matrix.Scaler.from_dict(d["scaler"]),
            d["meta"])
