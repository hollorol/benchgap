"""Multivariate gapfill: predict one benchmark from several source benchmarks.

For each target benchmark version, two searches run and the fit with the
lower leave-one-out error wins:

- the lasso part of one elastic net (`enet_mv`) over a pool of candidate
  sources decides the feature set. The pool grows by data availability:
  candidates are ranked (a source's own best single-source fit first, then
  the models it shares with the target) and a source joins only if the
  models measured on it and the pool so far still clear the minimum
  training size - a sparse candidate is skipped, not a cutoff hiding the
  candidates ranked below it. The penalty drives useless sources'
  coefficients to exactly zero; the survivors are the mapping's features.
- greedy forward selection, the previous technique: each step tries every
  remaining candidate on its own training set, both families competing per
  candidate set, the best leave-one-out error advancing.

On the features each search lands on, the fitted families then compete by
leave-one-out CV and the lower error is stored: the elastic net itself
(linear) and a **multivariate Michaelis-Menten** (`mm_mv`) that combines
the sources into a weighted aggregate capability index mapped through
y = y0 + Vmax*s/(K + s), monotone in every source. The penalty strength (a
fraction of the zeroing penalty) and the L1/L2 mix are chosen by LOO CV
over a grid, preferring the stronger setting on ties and keeping only
settings that leave at least the policy's minimum number of features in the
model. Predictions are clipped to [0, 1].

Training uses only measured scores. The same quality gate as the univariate
fit applies, and a stored fit must beat the target's best univariate
mapping. A fit the lasso leaves with a single source is still worth
storing - its shrinkage can beat every univariate curve - but a one-feature
nonlinear fit is the univariate pipeline's job. Targets whose best fit
fails the gates keep no multi-mapping and their gaps are filled (if at
all) by the univariate path.

fit_cross_multimappings runs the same combined search with sources of any
capability, for the multivariate view only (cross_multi_mappings, never used
for estimates): it always combines at least two, so the view can compare
them with the best one alone.
"""
from __future__ import annotations

import json
import sqlite3
from functools import partial

import numpy as np
from scipy.optimize import curve_fit

from .cache import FitCache, fit_key
from .fitting import _loo_indices
from .parallel import pmap

# candidates offered to one fit (the pool); the L1 part zeroes what it can
POOL_MAX = 8
# penalty strength as a fraction of the zeroing penalty, strongest first
# (ties keep the sparser fit)
ENET_FRACS = (0.5, 0.25, 0.1, 0.05, 0.02, 0.01, 0.005)
# the L1/L2 mix, pure lasso first
ENET_L1_RATIOS = (1.0, 0.9, 0.5)
ENET_MAX_ITER = 1000
ENET_TOL = 1e-10

# the families refitted on the lasso-selected features; the lower LOO error wins
MV_COMPETITORS = ("mm_mv",)
# both fitted per candidate set in the greedy search
MV_CANDIDATES = ("enet_mv", *MV_COMPETITORS)
# how many features the greedy search can stack
GREEDY_MAX_FEATURES = 3

# the cross search picks from the target's CROSS_POOL best single sources (of any capability)
# and the CROSS_POOL others that share the most models with it
CROSS_POOL = 8


def _min_train(min_pairs: int, n_features: int) -> int:
    """Minimum training size for a feature set; LOO guards the rest."""
    return min_pairs + 2 * n_features


# --- the elastic net ------------------------------------------------------------


def _penalties(lmax: float, frac: float, l1_ratio: float) -> tuple[float, float]:
    """(L1, L2) strengths: ``frac`` of the zeroing penalty ``lmax``, mixed by ``l1_ratio``."""
    l1 = frac * lmax
    return l1, l1 * (1.0 - l1_ratio) / l1_ratio


def _standardize(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(X centered and scaled, y centered, the x scales, the y mean); constant columns stay
    all-zero after centering, so the solver leaves them at coefficient 0."""
    x_std = X.std(axis=0)
    x_std = np.where(x_std < 1e-12, 1.0, x_std)
    y_mean = float(y.mean())
    return (X - X.mean(axis=0)) / x_std, y - y_mean, x_std, y_mean


def _lmax(X: np.ndarray, y: np.ndarray) -> float:
    """The smallest L1 penalty (on standardized features) that zeroes every coefficient."""
    Xs, yc, _, _ = _standardize(X, y)
    return float(np.max(np.abs(Xs.T @ yc)) / len(y))


def _enet_solve(Xs: np.ndarray, yc: np.ndarray, l1: float, l2: float) -> np.ndarray:
    """Coordinate descent on the standardized elastic net (the Gram matrix way):
    minimize 1/(2n)||yc - Xs w||^2 + l1||w||_1 + (l2/2)||w||^2."""
    n, k = Xs.shape
    G = (Xs.T @ Xs) / n
    c = (Xs.T @ yc) / n
    colsq = np.diag(G)
    denom = colsq + l2
    w = np.zeros(k)
    for _ in range(ENET_MAX_ITER):
        delta = 0.0
        for j in range(k):
            if colsq[j] <= 0.0:
                continue
            rho = c[j] - G[j] @ w + colsq[j] * w[j]
            if rho > l1:
                new = (rho - l1) / denom[j]
            elif rho < -l1:
                new = (rho + l1) / denom[j]
            else:
                new = 0.0
            if new != w[j]:
                delta = max(delta, abs(new - w[j]))
                w[j] = new
        if delta < ENET_TOL:
            break
    return w


def _enet_coefs(X: np.ndarray, y: np.ndarray, l1: float, l2: float) -> tuple[float, np.ndarray]:
    """(intercept, coefficients) of the elastic net at (l1, l2), back on the original scale."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    Xs, yc, x_std, y_mean = _standardize(X, y)
    w = _enet_solve(Xs, yc, l1, l2)
    coef = w / x_std
    return y_mean - float(X.mean(axis=0) @ coef), coef


def _enet_loo(
    X: np.ndarray, y: np.ndarray, frac: float, l1_ratio: float, indices=None
) -> np.ndarray:
    """Each point's (of ``indices``, default the capped LOO set) prediction by the elastic net
    at (frac, l1_ratio) fitted to the other points; NaN where that fit fails. The penalty is
    always a fraction of the training fold's own zeroing penalty, so the folds see the same
    relative shrinkage as the full fit."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    idx = _loo_indices(len(y)) if indices is None else np.asarray(indices)
    out = np.empty(len(idx))
    for pos, i in enumerate(idx):
        mask = np.ones(len(y), dtype=bool)
        mask[i] = False
        lmax = _lmax(X[mask], y[mask])
        if not lmax > 0.0:
            out[pos] = float("nan")
            continue
        intercept, coef = _enet_coefs(X[mask], y[mask], *_penalties(lmax, frac, l1_ratio))
        out[pos] = float(np.clip(intercept + X[i] @ coef, 0.0, 1.0))
    return out


def _fit_enet_mv(X: np.ndarray, y: np.ndarray, min_features: int = 1) -> dict:
    """The elastic net over the penalty grid whose lasso part keeps at least ``min_features``
    sources, with the lowest LOO CV error (the stronger setting wins ties). ``params`` holds
    the kept features' coefficients and the grid settings; ``keep`` the columns they belong to."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    lmax = _lmax(X, y)
    if not lmax > 0.0:
        raise RuntimeError("no source correlates with the target")
    best = None
    for l1_ratio in ENET_L1_RATIOS:
        for frac in ENET_FRACS:
            intercept, coef = _enet_coefs(X, y, *_penalties(lmax, frac, l1_ratio))
            keep = np.flatnonzero(coef)
            if len(keep) < min_features:
                continue
            pred = _enet_loo(X[:, keep], y, frac, l1_ratio)
            if np.isnan(pred).any():
                continue
            loo = float(np.sqrt(np.mean((pred - y[_loo_indices(len(y))]) ** 2)))
            if best is None or loo < best["LOO"] - 1e-12:
                best = {
                    "LOO": loo,
                    "frac": frac,
                    "l1_ratio": l1_ratio,
                    "intercept": float(intercept),
                    "coef": [float(coef[j]) for j in keep],
                    "keep": [int(j) for j in keep],
                }
    if best is None:
        raise RuntimeError(f"no penalty setting keeps {min_features} features")
    del best["LOO"]
    return best


def _enet_mv_predict(params: dict, X: np.ndarray) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    out = params["intercept"] + X @ np.asarray(params["coef"], dtype=float)
    return np.clip(out, 0.0, 1.0)


def _mm_mv_fn(X, y0, vmax, k, *w):
    X = np.atleast_2d(np.asarray(X, dtype=float))
    w = np.asarray(w, dtype=float)
    total = w.sum()
    w = w / total if total > 1e-9 else np.full(len(w), 1.0 / len(w))
    s = X @ w
    return y0 + vmax * s / (k + s)


def _fit_mm_mv(X: np.ndarray, y: np.ndarray, min_features: int = 1) -> dict:
    """The multivariate Michaelis-Menten on the features it is given: the weighted index
    s = sum(w_j * x_j), w >= 0, mapped through y = y0 + Vmax*s/(K + s), monotone in every
    source. The lasso part has already selected the features - it uses every column
    (``min_features`` does not apply)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    k_feat = X.shape[1]
    s0 = X.mean(axis=1)
    p0 = [float(np.min(y)), float(np.max(y) - np.min(y)),
          float(np.median(s0) * 0.1) + 1e-6] + [1.0 / k_feat] * k_feat
    bounds = (
        [0.0, 0.0, 1e-9] + [0.0] * k_feat,
        [1.0, 2.0, np.inf] + [10.0] * k_feat,
    )
    popt, _ = curve_fit(_mm_mv_fn, X, y, p0=p0, maxfev=40000, bounds=bounds)
    w = np.asarray(popt[3:])
    total = w.sum()
    w = w / total if total > 1e-9 else np.full(k_feat, 1.0 / k_feat)
    return {
        "y0": float(popt[0]),
        "vmax": float(popt[1]),
        "k": float(popt[2]),
        "weights": [float(v) for v in w],
    }


def _mm_mv_predict(params: dict, X: np.ndarray) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    s = X @ np.asarray(params["weights"], dtype=float)
    out = params["y0"] + params["vmax"] * s / (params["k"] + s)
    return np.clip(out, 0.0, 1.0)


MV_FITTERS = {
    "enet_mv": (_fit_enet_mv, _enet_mv_predict),
    "mm_mv": (_fit_mm_mv, _mm_mv_predict),
}


def fit_mv(method: str, X, y, min_features: int = 1) -> dict:
    return MV_FITTERS[method][0](np.asarray(X, dtype=float), np.asarray(y, dtype=float), min_features)


def predict_mv(method: str, params: dict, X) -> np.ndarray:
    return MV_FITTERS[method][1](params, np.asarray(X, dtype=float))


def mv_metrics(method: str, params: dict, X, y) -> dict:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(y)
    pred = predict_mv(method, params, X)
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    rmse = float(np.sqrt(ss_res / n))

    idx = _loo_indices(n)
    # NaN if any fold's fit fails
    err = y[idx] - np.array(loo_predictions(method, X, y, idx, params=params))
    return {"n": n, "R2": r2, "RMSE": rmse, "LOO_RMSE": float(np.sqrt(np.mean(err ** 2)))}


def loo_predictions(method: str, X, y, indices=None, params=None) -> list[float]:
    """The prediction of each point (of ``indices``, default all) by the fit to all the other
    points; NaN where that fit fails. For the elastic net with ``params`` (the stored fit's
    penalty settings) the coefficients are refitted per fold at those settings; otherwise the
    whole fit is redone per fold."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if method == "enet_mv" and params is not None:
        # the view stores every training model's prediction: no fold capping here
        all_points = np.arange(len(y)) if indices is None else np.asarray(indices)
        return [
            float(p) if np.isfinite(p) else float("nan")
            for p in _enet_loo(X, y, params["frac"], params["l1_ratio"], all_points)
        ]
    out = []
    for i in range(len(y)) if indices is None else indices:
        mask = np.ones(len(y), dtype=bool)
        mask[i] = False
        try:
            out.append(float(predict_mv(method, fit_mv(method, X[mask], y[mask]), X[i : i + 1])[0]))
        except (RuntimeError, np.linalg.LinAlgError, ValueError):
            out.append(float("nan"))
    return out


# --- database side -------------------------------------------------------------


def _training_set(
    conn: sqlite3.Connection, target_id: int, feature_ids: list[int]
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """X (n x k), y (n), model ids - models measured on target and all features."""
    joins = ""
    wheres = ""
    args: list = []
    for j, fid in enumerate(feature_ids):
        joins += f" JOIN scores sf{j} ON sf{j}.model_id = m.id AND sf{j}.source = 'measured'"
        wheres += f" AND sf{j}.version_id = ?"
        args.append(fid)
    rows = conn.execute(
        "SELECT m.id AS model_id, st.value AS y"
        + "".join(f", sf{j}.value AS f{j}" for j in range(len(feature_ids)))
        + f" FROM models m JOIN scores st ON st.model_id = m.id"
        " AND st.source = 'measured' AND st.version_id = ?"
        + joins
        + " WHERE 1=1" + wheres,
        [target_id] + args,
    ).fetchall()
    y = np.array([r["y"] for r in rows], dtype=float)
    X = np.array(
        [[r[f"f{j}"] for j in range(len(feature_ids))] for r in rows], dtype=float
    )
    model_ids = [r["model_id"] for r in rows]
    return X, y, model_ids


# what each worker's searches read: its own connection to the database (sqlite3 connections
# can't be shared across processes), the benchmark versions, the minimum overlap, a FitCache over
# the earlier runs' fits (whose "used" collects the fits of the target being searched) and the
# search's policy: where its sources come from and how many the lasso part must keep
_worker: dict = {}


def _init(
    db: str | sqlite3.Connection, versions: list[dict], min_pairs: int, earlier: dict,
    sources=None, min_features: int = 1, singles: dict | None = None,
) -> None:
    """A worker's state: ``db`` is the database's path, or in this process its open connection;
    ``sources(target)`` the versions its search picks from (default: _capability_sources);
    ``singles`` what both rank by (default: the stored fits)."""
    from .db import connect  # local import to avoid a cycle

    conn = connect(db, readonly=True) if isinstance(db, str) else db
    _worker.update(
        conn=conn, versions=versions, min_pairs=min_pairs, cache=FitCache.of(earlier),
        sources=sources or _capability_sources, min_features=min_features,
        singles=singles if singles is not None else _single_fits(conn),
    )


def _fit_scored(method: str, X: np.ndarray, y: np.ndarray, min_features: int = 1) -> list | None:
    """[params, the fitted columns' indices, metrics] of ``method`` on (X, y), or None if it
    does not fit (for the families that do not select - mm_mv uses every column - the indices
    are all of them); from the cache if it has them."""
    def fit():
        try:
            params = fit_mv(method, X, y, min_features)
            keep = params.pop("keep", list(range(X.shape[1])))
            return [params, keep, mv_metrics(method, params, X[:, keep], y)]
        except (RuntimeError, np.linalg.LinAlgError, ValueError):
            return None

    return _worker["cache"].get(fit_key(method, X, y, min_features), fit)


def _search_cached(target: dict) -> tuple[dict | None, dict]:
    """_search's result and the fits it used (for the cache)."""
    cache = _worker["cache"]
    cache.used = {}
    return _search(target), cache.used


def _capability_sources(target: dict) -> list[dict]:
    """The versions of the target's capability with at least min_pairs models shared with it,
    ranked: the best single-source fits first, then the most shared models."""
    from .fit import _paired_scores  # local import to avoid a cycle

    conn, min_pairs = _worker["conn"], _worker["min_pairs"]
    singles = _worker["singles"].get(target["id"], {})
    ranked = []
    for v in _worker["versions"]:
        if v["id"] == target["id"] or v["capability"] != target["capability"]:
            continue
        shared = len(_paired_scores(conn, target["id"], v["id"]))
        if shared < min_pairs:
            continue
        loo = singles.get(v["id"], (None, 0))[0]
        ranked.append((loo if loo is not None else float("inf"), -shared, v["id"], v))
    return [v for *_, v in sorted(ranked, key=lambda t: t[:3])]


def _single_fits(conn: sqlite3.Connection) -> dict[int, dict[int, tuple[float, int]]]:
    """{target: {source: (LOO RMSE, shared models)}} of the stored single-source fits:
    the mappings and the cross-capability fits."""
    out: dict = {}
    for to, frm, loo, n in conn.execute(
        "SELECT to_version_id, from_version_id, MIN(json_extract(metrics_json, '$.LOO_RMSE')), MAX(n_points) FROM"
        " (SELECT from_version_id, to_version_id, metrics_json, n_points FROM mappings"
        "  UNION ALL SELECT from_version_id, to_version_id, metrics_json, n_points FROM cross_mappings)"
        " GROUP BY to_version_id, from_version_id"
    ):
        if loo is not None:
            out.setdefault(to, {})[frm] = (loo, n)
    return out


def _cross_sources(target: dict) -> list[dict]:
    """The target's CROSS_POOL best single sources of any capability, and the CROSS_POOL others
    sharing the most models with it (a combination needs models measured on all of its benchmarks)."""
    singles = _worker["singles"].get(target["id"], {})
    fitted = [v for v in _worker["versions"] if v["id"] in singles]
    pool = sorted(fitted, key=lambda v: singles[v["id"]][0])[:CROSS_POOL]
    return pool + [v for v in sorted(fitted, key=lambda v: -singles[v["id"]][1]) if v not in pool][:CROSS_POOL]


def _pool(target: dict) -> tuple[list[dict], np.ndarray, np.ndarray, list[int]]:
    """The target's candidate sources (best ranked first) grown greedily by data
    availability: a source joins the pool only if the training set - the models
    measured on the target and every pool member - still clears the minimum
    training size; a source too sparse to combine with is skipped, not a cutoff
    that would hide the better candidates ranked below it. The lasso part then
    selects among all of them. Returns (pool, X, y, model ids); the pool is empty
    when no source clears the minimum alone."""
    conn, min_pairs = _worker["conn"], _worker["min_pairs"]
    pool: list[dict] = []
    X = y = None
    models: list[int] = []
    for cand in _worker["sources"](target)[:POOL_MAX]:
        trial = pool + [cand]
        X_t, y_t, models_t = _training_set(conn, target["id"], [v["id"] for v in trial])
        if len(y_t) >= _min_train(min_pairs, len(trial)):
            pool, X, y, models = trial, X_t, y_t, models_t
    return pool, X, y, models


def _pool_search(target: dict) -> dict | None:
    """The current technique: the elastic net's lasso part selects the features from a pool
    of sources, the families compete on them and the lower LOO error wins (None: no usable
    fit)."""
    pool, X, y, models = _pool(target)
    if len(pool) < _worker["min_features"]:
        return None
    fitted = _fit_scored("enet_mv", X, y, _worker["min_features"])
    if fitted is None:
        return None
    params, keep, metrics = fitted
    features = [pool[j] for j in keep]
    X_sel = X[:, keep]
    best = {
        "features": features, "method": "enet_mv", "params": params,
        "metrics": metrics, "X": X_sel, "y": y, "models": models,
    }
    if len(features) >= 2:  # only a genuinely multi-input fit is worth the refit
        for method in MV_COMPETITORS:
            fitted_mv = _fit_scored(method, X_sel, y)
            if fitted_mv is None:
                continue
            mv_params, _, mv_metrics = fitted_mv
            if mv_metrics["LOO_RMSE"] < metrics["LOO_RMSE"] - 1e-12:
                best = {
                    "features": features, "method": method, "params": mv_params,
                    "metrics": mv_metrics, "X": X_sel, "y": y, "models": models,
                }
    return best


def _step(target: dict, selected: list[dict], sources: list[dict]) -> dict | None:
    """One greedy step: the best fit of the selected features plus one more of the sources,
    each candidate set on its own training set, both families competing (None: none fits).
    The elastic net is pinned to every candidate feature - here the step selects, not the
    lasso."""
    conn, min_pairs = _worker["conn"], _worker["min_pairs"]
    best = None
    for s in sources:
        if any(s["id"] == f["id"] for f in selected):
            continue
        feats = selected + [s]
        X, y, models = _training_set(conn, target["id"], [f["id"] for f in feats])
        if len(y) < _min_train(min_pairs, len(feats)):
            continue
        for method in MV_CANDIDATES:
            fitted = _fit_scored(method, X, y, len(feats))
            if fitted is None:
                continue
            params, keep, metrics = fitted
            if best is None or metrics["LOO_RMSE"] < best["metrics"]["LOO_RMSE"]:
                best = {"features": feats, "method": method, "params": params,
                        "metrics": metrics, "X": X[:, keep], "y": y, "models": models}
    return best


def _greedy_search(target: dict) -> dict | None:
    """The previous technique: greedy forward selection - each step tries every remaining
    candidate, both families per candidate set, the best leave-one-out error advancing,
    a third feature only if it helps (None: no usable fit)."""
    sources = _worker["sources"](target)
    min_features = _worker["min_features"]
    steps: list[dict] = []
    while len(steps) < GREEDY_MAX_FEATURES:
        step = _step(target, steps[-1]["features"] if steps else [], sources)
        if step is None:
            if not steps or len(steps) >= min_features:
                break
            # nothing more shares enough models with the last pick: carry on without it
            dropped = steps.pop()["features"][-1]
            sources = [v for v in sources if v["id"] != dropped["id"]]
        elif len(steps) < min_features or step["metrics"]["LOO_RMSE"] < steps[-1]["metrics"]["LOO_RMSE"] - 1e-4:
            steps.append(step)
        else:
            break
    return steps[-1] if steps else None


def _search(target: dict) -> dict | None:
    """The target's best fit, combining the two searches: the lasso part of one elastic
    net over an availability-grown pool selects features, and greedy forward selection
    tries every candidate set with both families; the lower LOO error wins, ties keeping
    the lasso's (None: neither finds a usable fit)."""
    pool_best = _pool_search(target)
    greedy_best = _greedy_search(target)
    if greedy_best is None:
        return pool_best
    if pool_best is None:
        return greedy_best
    if pool_best["metrics"]["LOO_RMSE"] <= greedy_best["metrics"]["LOO_RMSE"]:
        return pool_best
    return greedy_best


def _fraction_versions(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT v.id, v.version, v.harness, b.name AS benchmark,"
            "       b.capability AS capability"
            " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
            " WHERE v.unit = 'fraction'"
        )
    ]


def _run_searches(conn: sqlite3.Connection, fn, min_pairs: int, cache: FitCache, jobs: int | None, **policy):
    """(target, fn(target)'s result) for every fraction version, searched on ``jobs`` processes
    (default: every core) under ``policy`` (_init's); the fits they used go into ``cache``."""
    versions = _fraction_versions(conn)
    conn.commit()  # the workers read the committed database
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    # an in-memory database (no path) exists only in this process
    for target, (best, used) in zip(versions, pmap(
        fn, versions, jobs if path else 1, partial(_init, **policy), (path or conn, versions, min_pairs, cache.earlier)
    )):
        cache.used.update(used)
        yield target, best


def fit_multimappings(
    conn: sqlite3.Connection,
    min_pairs: int,
    min_r2: float,
    max_loo_rmse: float,
    jobs: int | None = None,
    cache: FitCache | None = None,
) -> list[dict]:
    """Per-target multivariate fits: two searches per target (the lasso part of one
    elastic net over a pool, and greedy forward selection), the families competing on
    what each lands on, and the best fit stored; returns a summary list.

    The targets' searches run on ``jobs`` processes (default: every
    core), each reading the database through its own connection; a fit whose
    training data is in ``cache`` is reused.
    """
    from .fit import gate_failure  # local import to avoid a cycle

    summary = []
    for target, best in list(_run_searches(conn, _search_cached, min_pairs, cache or FitCache(None, "multifit"), jobs)):
        # this run replaces whatever an earlier one stored for this target - even
        # a rejected fit must clear the old row, or gapfill would predict from a
        # fit this pipeline no longer knows; drop gapfilled rows that referenced
        # it (gapfill will re-fill)
        conn.execute(
            "DELETE FROM scores WHERE source = 'gapfilled' AND multi_mapping_id IN"
            " (SELECT id FROM multi_mappings WHERE to_version_id = ?)",
            (target["id"],),
        )
        conn.execute("DELETE FROM multi_mappings WHERE to_version_id = ?", (target["id"],))
        if best is None:
            continue
        if len(best["features"]) < 2 and best["method"] != "enet_mv":
            # a one-feature nonlinear fit is the univariate pipeline's job;
            # the lasso's shrunk single-source fit is genuinely new - its
            # shrinkage can beat every univariate curve - so only that may be
            # stored with a single feature
            continue

        # a multi-mapping must beat the target's best univariate mapping,
        # otherwise gapfill would never select it
        uni_loo = conn.execute(
            "SELECT COALESCE(MIN(COALESCE(json_extract(metrics_json, '$.LOO_RMSE'), 1e9)), 1e9)"
            " FROM mappings WHERE to_version_id = ?",
            (target["id"],),
        ).fetchone()[0]
        if (
            uni_loo < 1e9
            and best["metrics"]["LOO_RMSE"] >= uni_loo - 1e-6
        ):
            summary.append(
                {
                    "target": _label(target),
                    "method": None,
                    "rejected": f"LOO RMSE {best['metrics']['LOO_RMSE'] * 100:.2f}pp"
                    f" does not beat best univariate {uni_loo * 100:.2f}pp",
                    "n": int(best["metrics"]["n"]),
                    "features": [_label(f) for f in best["features"]],
                }
            )
            continue
        rejected = gate_failure(best["metrics"], min_r2, max_loo_rmse)
        if rejected:
            summary.append(
                {
                    "target": _label(target),
                    "method": None,
                    "rejected": rejected,
                    "n": int(best["metrics"]["n"]),
                    "features": [_label(f) for f in best["features"]],
                }
            )
            continue

        fids = [f["id"] for f in best["features"]]
        train_ranges = {
            str(fid): [float(best["X"][:, j].min()), float(best["X"][:, j].max())]
            for j, fid in enumerate(fids)
        }
        conn.execute(
            "INSERT INTO multi_mappings (to_version_id, method, feature_version_ids_json,"
            " params_json, metrics_json, n_points, train_ranges_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                target["id"],
                best["method"],
                json.dumps(fids),
                json.dumps(best["params"]),
                json.dumps(best["metrics"]),
                len(best["y"]),
                json.dumps(train_ranges),
            ),
        )
        summary.append(
            {
                "target": _label(target),
                "method": best["method"],
                "features": [_label(f) for f in best["features"]],
                "n": len(best["y"]),
                "R2": best["metrics"]["R2"],
                "LOO_RMSE": best["metrics"]["LOO_RMSE"],
            }
        )
    conn.commit()
    return summary


def _search_scored(target: dict) -> tuple[dict | None, dict]:
    """_search_cached's, with every model's leave-one-out prediction by the best fit and the
    LOO RMSE of the best of its features alone (either family), on the same models."""
    best, used = _search_cached(target)
    if best is not None:
        method, X, y, params = best["method"], best["X"], best["y"], best["params"]
        best["loo_pred"] = _worker["cache"].get(
            fit_key("loo_pred", method, X, y, json.dumps(params, sort_keys=True)),
            lambda: loo_predictions(method, X, y, params=params),
        )
        alone = []
        for j in range(X.shape[1]):
            for m in ("enet_mv", *MV_COMPETITORS):
                fitted = _fit_scored(m, X[:, [j]], y)
                if fitted is not None and np.isfinite(fitted[2]["LOO_RMSE"]):
                    alone.append(fitted[2]["LOO_RMSE"])
        best["alone_loo"] = min(alone, default=None)
    return best, used


def fit_cross_multimappings(
    conn: sqlite3.Connection,
    min_pairs: int,
    min_r2: float,
    max_loo_rmse: float,
    jobs: int | None = None,
    cache: FitCache | None = None,
) -> list[dict]:
    """For the multivariate view only: each target's best fit from source benchmarks of any
    capability (_cross_sources) - at least two of them, by the lasso part of one elastic net -
    stored in cross_multi_mappings with each model's leave-one-out prediction and whether it
    passes the quality gate. No estimate comes from them. Replaces the earlier ones; returns
    a summary list.

    Run after fit and crossfit: the sources are ranked by their fits.
    """
    from .db import version_label
    from .fit import gate_failure  # local imports to avoid a cycle

    singles = _single_fits(conn)
    conn.execute("DELETE FROM cross_multi_mappings")
    summary = []
    for target, best in list(_run_searches(
        conn, _search_scored, min_pairs, cache or FitCache(None, "crossmultifit"), jobs,
        sources=_cross_sources, min_features=2, singles=singles,
    )):
        if best is None:
            continue
        m = best["metrics"]
        rejected = gate_failure(m, min_r2, max_loo_rmse)
        points = [[mid, float(obs), p if np.isfinite(p) else None] for mid, obs, p in zip(best["models"], best["y"], best["loo_pred"])]
        conn.execute(
            "INSERT INTO cross_multi_mappings (to_version_id, method, feature_version_ids_json,"
            " params_json, metrics_json, n_points, points_json, alone_loo, passes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (target["id"], best["method"], json.dumps([f["id"] for f in best["features"]]),
             json.dumps(best["params"]), json.dumps(m), len(best["y"]), json.dumps(points),
             best["alone_loo"], int(rejected is None)),
        )
        summary.append({
            "target": version_label(target),
            "method": best["method"],
            "features": [version_label(f) for f in best["features"]],
            "n": len(best["y"]),
            "R2": m["R2"],
            "LOO_RMSE": m["LOO_RMSE"],
            "alone_LOO_RMSE": best["alone_loo"],
            "rejected": rejected,
        })
    conn.commit()
    return summary


def _label(v: sqlite3.Row) -> str:
    return f"{v['benchmark']}/{v['version']}"
