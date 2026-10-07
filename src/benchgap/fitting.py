"""Candidate score-mapping models, fitting, and model selection.

Each candidate maps a score on one benchmark version (x) to a score on
another (y), both fractions in [0, 1]. Fits are deterministic least-squares;
the registry pattern lets a probabilistic method register itself later with
the same interface (fit -> params, predict(params, x)).

Candidates:
- linear:      y = a*x + b
- quadratic:   y = a*x^2 + b*x + c   (known-bad: non-monotone, kept for comparison)
- mm:          y = Vmax*x / (K + x)             Michaelis-Menten through origin
- mm_offset:   y = y0 + Vmax*x / (K + x)        Michaelis-Menten with offset

mm_offset is the expected winner on saturating score relationships: monotone
with a ceiling, unlike the quadratic which eventually turns down.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.optimize import curve_fit


# --- candidate model forms ----------------------------------------------------


def _linear(x, a, b):
    return a * x + b


def _quadratic(x, a, b, c):
    return a * x**2 + b * x + c


def _mm(x, vmax, k):
    return vmax * x / (k + x)


def _mm_offset(x, y0, vmax, k):
    return y0 + vmax * x / (k + x)


@dataclass(frozen=True)
class Candidate:
    method: str
    fn: Callable
    param_names: tuple[str, ...]
    p0: Callable[[np.ndarray, np.ndarray], list[float]]
    bounds: tuple[list[float], list[float]] | None = None


def _sorted_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(x)
    return x[order], y[order]


def _linear_p0(x, y):
    xs, ys = _sorted_xy(x, y)
    span = max(xs[-1] - xs[0], 1e-9)
    return [float((ys[-1] - ys[0]) / span), float(ys[0])]


def _quadratic_p0(x, y):
    return [0.0, *_linear_p0(x, y)]


def _mm_p0(x, y):
    return [float(np.max(y)), float(np.median(x) * 0.1) + 1e-6]


def _mm_offset_p0(x, y):
    return [float(np.min(y)), float(np.max(y) - np.min(y)), float(np.median(x) * 0.1) + 1e-6]


# Bounds keep the saturating fits physical in fraction space: non-negative
# baseline and rise, half-saturation strictly positive, asymptote at most 2.
_MM_BOUNDS = ([0.0, 0.0, 1e-9], [1.0, 2.0, np.inf])
_MM_PLAIN_BOUNDS = ([0.0, 1e-9], [2.0, np.inf])

CANDIDATES: dict[str, Candidate] = {
    c.method: c
    for c in [
        Candidate("linear", _linear, ("slope", "intercept"), _linear_p0),
        Candidate("quadratic", _quadratic, ("a", "b", "c"), _quadratic_p0),
        Candidate("mm", _mm, ("vmax", "k"), _mm_p0, _MM_PLAIN_BOUNDS),
        Candidate("mm_offset", _mm_offset, ("y0", "vmax", "k"), _mm_offset_p0, _MM_BOUNDS),
    ]
}


@dataclass
class FitResult:
    method: str
    params: dict[str, float]
    metrics: dict[str, float]

    @property
    def n_params(self) -> int:
        return len(self.params)


def _fit_one(method: str, x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    cand = CANDIDATES[method]
    kwargs = {}
    if cand.bounds is not None:
        kwargs["bounds"] = cand.bounds
    popt, _ = curve_fit(
        cand.fn, x, y, p0=cand.p0(x, y), maxfev=20000, **kwargs
    )
    return {name: float(v) for name, v in zip(cand.param_names, popt)}


def _predict(method: str, params: dict[str, float], x) -> np.ndarray:
    cand = CANDIDATES[method]
    x = np.atleast_1d(np.asarray(x, dtype=float))
    args = [params[n] for n in cand.param_names]
    return cand.fn(x, *args)


def fit_metrics(method: str, params: dict[str, float], x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """In-sample metrics plus leave-one-out CV RMSE."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    p = len(params)
    pred = _predict(method, params, x)
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / (n - p) if n > p else float("nan")
    rmse = float(np.sqrt(ss_res / n))

    loo_sq = 0.0
    loo_ok = n > p
    if loo_ok:
        for i in range(n):
            mask = np.ones(n, dtype=bool)
            mask[i] = False
            try:
                params_i = _fit_one(method, x[mask], y[mask])
                loo_sq += float((y[i] - _predict(method, params_i, x[i : i + 1])[0]) ** 2)
            except RuntimeError:
                loo_ok = False
                break
    loo_rmse = float(np.sqrt(loo_sq / n)) if loo_ok else float("nan")
    return {
        "n": n,
        "n_params": p,
        "R2": r2,
        "adj_R2": adj_r2,
        "RMSE": rmse,
        "LOO_RMSE": loo_rmse,
    }


def fit_candidate(method: str, x, y) -> FitResult:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    params = _fit_one(method, x, y)
    metrics = fit_metrics(method, params, x, y)
    return FitResult(method=method, params=params, metrics=metrics)


def fit_all(x, y) -> list[FitResult]:
    """Fit every registered candidate on the same paired data."""
    return [fit_candidate(m, x, y) for m in CANDIDATES]


def select_best(results: list[FitResult]) -> FitResult:
    """Selection rule: lowest leave-one-out CV RMSE (falls back to RMSE)."""
    keyed = [(r.metrics.get("LOO_RMSE"), r.metrics["RMSE"], i, r) for i, r in enumerate(results)]
    keyed.sort(key=lambda t: (np.isnan(t[0]), t[0] if not np.isnan(t[0]) else t[1]))
    return keyed[0][3]


def predict(method: str, params: dict[str, float], x) -> np.ndarray:
    return _predict(method, params, x)
