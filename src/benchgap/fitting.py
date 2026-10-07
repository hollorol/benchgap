"""Candidate score-mapping models, fitting, and model selection.

Each candidate maps a score on one benchmark version (x) to a score on
another (y), both fractions in [0, 1]. Fits are deterministic least-squares;
the registry pattern lets a probabilistic method register itself later with
the same interface (fit -> params, predict(params, x)).

Candidates:
- linear:       y = a*x + b
- mm:           y = Vmax*x / (K + x)             Michaelis-Menten through origin
- mm_offset:    y = y0 + Vmax*x / (K + x)         Michaelis-Menten with offset
- mm_offset_inv: the analytic inverse of a forward mm_offset fit

mm_offset is monotone with a ceiling, so its inverse exists in closed form;
for the reverse direction of a fitted pair we use that inverse instead of an
independently fitted (non-monotone) polynomial. All candidates here are
monotone non-decreasing.

Leave-one-out CV is capped at MAX_LOO_FOLDS folds (deterministic subsample)
so that large benchmark pairs stay tractable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.optimize import curve_fit

# Maximum number of leave-one-out folds actually refit; above this, a
# deterministic subsample of the points is used.
MAX_LOO_FOLDS = 40


# --- model forms --------------------------------------------------------------


def _linear(x, a, b):
    return a * x + b


def _mm(x, vmax, k):
    return vmax * x / (k + x)


def _mm_offset(x, y0, vmax, k):
    return y0 + vmax * x / (k + x)


@dataclass(frozen=True)
class Candidate:
    method: str
    param_names: tuple[str, ...]
    fit: Callable[[np.ndarray, np.ndarray], dict[str, float]]
    predict: Callable[[dict[str, float], np.ndarray], np.ndarray]
    equation: str


def _sorted_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(x)
    return x[order], y[order]


def _lsq_candidate(method, fn, param_names, p0_fn, equation, bounds=None) -> Candidate:
    def fit(x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        kwargs = {"bounds": bounds} if bounds is not None else {}
        popt, _ = curve_fit(fn, x, y, p0=p0_fn(x, y), maxfev=20000, **kwargs)
        return {n: float(v) for n, v in zip(param_names, popt)}

    def predict(params, x):
        x = np.atleast_1d(np.asarray(x, dtype=float))
        args = [params[n] for n in param_names]
        return fn(x, *args)

    return Candidate(method, tuple(param_names), fit, predict, equation)


def _linear_p0(x, y):
    xs, ys = _sorted_xy(x, y)
    span = max(xs[-1] - xs[0], 1e-9)
    return [float((ys[-1] - ys[0]) / span), float(ys[0])]


def _mm_p0(x, y):
    return [float(np.max(y)), float(np.median(x) * 0.1) + 1e-6]


def _mm_offset_p0(x, y):
    return [
        float(np.min(y)),
        float(np.max(y) - np.min(y)),
        float(np.median(x) * 0.1) + 1e-6,
    ]


# Bounds keep the saturating fits physical in fraction space: non-negative
# baseline and rise, half-saturation strictly positive, asymptote at most 2.
_MM_BOUNDS = ([0.0, 0.0, 1e-9], [1.0, 2.0, np.inf])
_MM_PLAIN_BOUNDS = ([0.0, 1e-9], [2.0, np.inf])


def _fit_mm_offset(x, y) -> dict[str, float]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    popt, _ = curve_fit(
        _mm_offset, x, y, p0=_mm_offset_p0(x, y), maxfev=20000, bounds=_MM_BOUNDS
    )
    return {"y0": float(popt[0]), "vmax": float(popt[1]), "k": float(popt[2])}


def _mm_offset_predict(params, x) -> np.ndarray:
    x = np.atleast_1d(np.asarray(x, dtype=float))
    return _mm_offset(x, params["y0"], params["vmax"], params["k"])


def _mm_offset_inv_fn(x, y0, vmax, k):
    """Inverse MM+offset curve: u = K*(x - y0) / (y0 + Vmax - x), in [0, 1].

    This is the analytic inverse form of y = y0 + Vmax*u/(K + u), used as a
    model family for directions where the relationship is convex (the inverse
    of a saturating curve). Values are clamped to [0, 1]; for x <= y0 the
    curve returns 0, for x >= y0 + Vmax (at/above the ceiling) it returns 1.
    """
    x = np.atleast_1d(np.asarray(x, dtype=float))
    asymptote = y0 + vmax
    num = k * (x - y0)
    den = asymptote - x
    with np.errstate(divide="ignore", invalid="ignore"):
        u = np.where(den > 1e-9, num / np.where(den > 1e-9, den, 1.0), np.inf)
    u = np.where(x <= y0, 0.0, u)
    return np.clip(u, 0.0, 1.0)


def _mm_offset_inv_fit(x, y) -> dict[str, float]:
    """Least-squares fit of the inverse MM+offset family in the target space.

    Fitting the inverse *form* directly (rather than inverting the forward
    fit's point predictions) avoids error amplification near the ceiling:
    a forward fit saturates below the highest observed scores, and naive
    inversion of those maps them to nonsense.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x_min, x_max = float(np.min(x)), float(np.max(x))
    y0_0 = max(x_min - 0.05, 0.0)
    asymptote_0 = min(x_max + 0.05, 1.2)
    # seed k from the median point: y_med ~= k*(xm - y0)/(A - xm)
    order = np.argsort(x)
    xm = float(x[order[len(order) // 2]])
    ym = float(y[order[len(y) // 2]])
    k_0 = max(ym * (asymptote_0 - xm) / max(xm - y0_0, 1e-3), 1e-3)
    popt, _ = curve_fit(
        _mm_offset_inv_fn,
        x,
        y,
        p0=[y0_0, asymptote_0 - y0_0, k_0],
        maxfev=40000,
        bounds=([0.0, 0.0, 1e-9], [1.0, 2.0, 20.0]),
    )
    return {"y0": float(popt[0]), "vmax": float(popt[1]), "k": float(popt[2])}


CANDIDATES: dict[str, Candidate] = {
    "linear": _lsq_candidate(
        "linear", _linear, ("slope", "intercept"), _linear_p0,
        "y = {slope:.4f}·x + {intercept:.4f}",
    ),
    "mm": _lsq_candidate(
        "mm", _mm, ("vmax", "k"), _mm_p0,
        "y = {vmax:.4f}·x / ({k:.5f} + x)",
        _MM_PLAIN_BOUNDS,
    ),
    "mm_offset": Candidate(
        "mm_offset",
        ("y0", "vmax", "k"),
        _fit_mm_offset,
        _mm_offset_predict,
        "y = {y0:.4f} + {vmax:.4f}·x / ({k:.5f} + x)",
    ),
    "mm_offset_inv": Candidate(
        "mm_offset_inv",
        ("y0", "vmax", "k"),
        _mm_offset_inv_fit,
        lambda params, x: _mm_offset_inv_fn(x, params["y0"], params["vmax"], params["k"]),
        "y = {k:.5f}·(x − {y0:.4f}) / ({y0:.4f} + {vmax:.4f} − x)",
    ),
}


@dataclass
class FitResult:
    method: str
    params: dict[str, float]
    metrics: dict[str, float]

    @property
    def n_params(self) -> int:
        return len(self.params)


def _loo_indices(n: int) -> np.ndarray:
    if n <= MAX_LOO_FOLDS:
        return np.arange(n)
    return np.linspace(0, n - 1, MAX_LOO_FOLDS, dtype=int)


def fit_metrics(
    method: str, params: dict[str, float], x: np.ndarray, y: np.ndarray
) -> dict[str, float]:
    """In-sample metrics plus (capped) leave-one-out CV RMSE."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    p = len(params)
    cand = CANDIDATES[method]
    pred = cand.predict(params, x)
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / (n - p) if n > p else float("nan")
    rmse = float(np.sqrt(ss_res / n))

    loo_sq = 0.0
    loo_ok = n > p
    if loo_ok:
        for i in _loo_indices(n):
            mask = np.ones(n, dtype=bool)
            mask[i] = False
            try:
                params_i = cand.fit(x[mask], y[mask])
                loo_sq += float((y[i] - cand.predict(params_i, x[i : i + 1])[0]) ** 2)
            except RuntimeError:
                loo_ok = False
                break
    loo_rmse = float(np.sqrt(loo_sq / len(_loo_indices(n)))) if loo_ok else float("nan")
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
    cand = CANDIDATES[method]
    params = cand.fit(x, y)
    metrics = fit_metrics(method, params, x, y)
    return FitResult(method=method, params=params, metrics=metrics)


def fit_all(x, y) -> list[FitResult]:
    """Fit every registered candidate on the same paired data."""
    return [fit_candidate(m, x, y) for m in CANDIDATES]


def select_best(results: list[FitResult]) -> FitResult:
    """Selection rule: lowest leave-one-out CV RMSE (falls back to RMSE)."""
    keyed = [
        (r.metrics.get("LOO_RMSE"), r.metrics["RMSE"], i, r)
        for i, r in enumerate(results)
    ]
    keyed.sort(key=lambda t: (np.isnan(t[0]) if t[0] is not None else False, t[0] if t[0] is not None and not np.isnan(t[0]) else t[1]))
    return keyed[0][3]


def predict(method: str, params: dict[str, float], x) -> np.ndarray:
    return CANDIDATES[method].predict(params, x)
