"""Multivariate gapfill: predict one benchmark from several source benchmarks.

For each target benchmark version, greedy forward selection picks up to
MAX_FEATURES same-capability source versions whose measured scores together
predict the target best by leave-one-out CV. Two monotone-leaning model
families are fitted per candidate feature set:

- linear_mv: ridge regression (alpha chosen by inner LOO over a grid);
  predictions clipped to [0, 1]
- mm_mv: multivariate Michaelis-Menten: the source scores are combined into
  an aggregate capability index s = sum(w_j * x_j) with w >= 0, mapped through
  y = y0 + Vmax*s/(K + s); monotone in every source

Training uses only measured scores. The same quality gate as the univariate
fit applies; targets whose best multivariate fit fails the gate keep no
multi-mapping and their gaps are filled (if at all) by the univariate path.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np
from scipy.optimize import curve_fit

from .fitting import _loo_indices
from .parallel import pmap

MV_CANDIDATES = ("linear_mv", "mm_mv")

# Feature-set size and minimum training size (grows with feature count).
MAX_FEATURES = 3
RIDGE_ALPHAS = (1e-4, 1e-3, 1e-2, 1e-1, 1.0)


def _min_train(min_pairs: int, n_features: int) -> int:
    """Minimum training size for a feature set; LOO guards the rest."""
    return min_pairs + 2 * n_features


def _ridge_solve(X: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    """Ridge with unpenalized intercept (first augmented column)."""
    Z = np.column_stack([np.ones(len(y)), X])
    D = np.eye(Z.shape[1])
    D[0, 0] = 0.0
    return np.linalg.solve(Z.T @ Z + alpha * D, Z.T @ y)


def _ridge_loo(X: np.ndarray, y: np.ndarray, alpha: float) -> float:
    n = len(y)
    sq = 0.0
    for i in _loo_indices(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        w = _ridge_solve(X[mask], y[mask], alpha)
        pred = w[0] + X[i] @ w[1:]
        sq += float((y[i] - pred) ** 2)
    return float(np.sqrt(sq / len(_loo_indices(n))))


def _fit_linear_mv(X: np.ndarray, y: np.ndarray) -> dict:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    best_alpha, best_loo = None, np.inf
    for alpha in RIDGE_ALPHAS:
        try:
            loo = _ridge_loo(X, y, alpha)
        except np.linalg.LinAlgError:
            continue
        if loo < best_loo:
            best_alpha, best_loo = alpha, loo
    if best_alpha is None:
        raise RuntimeError("ridge fit failed for all alphas")
    w = _ridge_solve(X, y, best_alpha)
    return {
        "alpha": float(best_alpha),
        "intercept": float(w[0]),
        "coef": [float(c) for c in w[1:]],
    }


def _linear_mv_predict(params: dict, X: np.ndarray) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    out = params["intercept"] + X @ np.asarray(params["coef"])
    return np.clip(out, 0.0, 1.0)


def _mm_mv_fn(X, y0, vmax, k, *w):
    X = np.atleast_2d(np.asarray(X, dtype=float))
    w = np.asarray(w, dtype=float)
    total = w.sum()
    w = w / total if total > 1e-9 else np.full(len(w), 1.0 / len(w))
    s = X @ w
    return y0 + vmax * s / (k + s)


def _fit_mm_mv(X: np.ndarray, y: np.ndarray) -> dict:
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
    s = X @ np.asarray(params["weights"])
    out = params["y0"] + params["vmax"] * s / (params["k"] + s)
    return np.clip(out, 0.0, 1.0)


MV_FITTERS = {
    "linear_mv": (_fit_linear_mv, _linear_mv_predict),
    "mm_mv": (_fit_mm_mv, _mm_mv_predict),
}


def fit_mv(method: str, X, y) -> dict:
    return MV_FITTERS[method][0](np.asarray(X, dtype=float), np.asarray(y, dtype=float))


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

    loo_sq = 0.0
    for i in _loo_indices(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        try:
            params_i = fit_mv(method, X[mask], y[mask])
            loo_sq += float((y[i] - predict_mv(method, params_i, X[i : i + 1])[0]) ** 2)
        except (RuntimeError, np.linalg.LinAlgError):
            return {"n": n, "R2": r2, "RMSE": rmse, "LOO_RMSE": float("nan")}
    return {
        "n": n,
        "R2": r2,
        "RMSE": rmse,
        "LOO_RMSE": float(np.sqrt(loo_sq / len(_loo_indices(n)))),
    }


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
# can't be shared across processes), the benchmark versions and the minimum overlap
_worker: dict = {}


def _init(db: str | sqlite3.Connection, versions: list[dict], min_pairs: int) -> None:
    """A worker's state: ``db`` is the database's path, or in this process its open connection."""
    from .db import connect  # local import to avoid a cycle

    _worker.update(conn=connect(db, readonly=True) if isinstance(db, str) else db, versions=versions, min_pairs=min_pairs)


def _search(target: dict) -> dict | None:
    """Greedy forward selection of the target's best feature set (None: no usable fit)."""
    from .fit import _paired_scores  # local import to avoid a cycle

    conn, versions, min_pairs = _worker["conn"], _worker["versions"], _worker["min_pairs"]
    sources = [
        v
        for v in versions
        if v["id"] != target["id"]
        and v["capability"] == target["capability"]
        and len(_paired_scores(conn, target["id"], v["id"])) >= min_pairs
    ]
    selected: list[dict] = []
    best = None
    while sources and len(selected) < MAX_FEATURES:
        step_best = None
        for s in sources:
            if any(s["id"] == f["id"] for f in selected):
                continue
            feats = selected + [s]
            fids = [f["id"] for f in feats]
            X, y, _ = _training_set(conn, target["id"], fids)
            if len(y) < _min_train(min_pairs, len(fids)):
                continue
            for method in MV_CANDIDATES:
                try:
                    params = fit_mv(method, X, y)
                    metrics = mv_metrics(method, params, X, y)
                except (RuntimeError, np.linalg.LinAlgError, ValueError):
                    continue
                loo = metrics.get("LOO_RMSE")
                if loo is None or not np.isfinite(loo):
                    continue
                if step_best is None or loo < step_best["metrics"]["LOO_RMSE"]:
                    step_best = {
                        "features": feats,
                        "method": method,
                        "params": params,
                        "metrics": metrics,
                        "X": X,
                        "y": y,
                    }
        if step_best is None:
            break
        if best is None or step_best["metrics"]["LOO_RMSE"] < best["metrics"]["LOO_RMSE"] - 1e-4:
            best = step_best
            selected = step_best["features"]
        else:
            break
    return best


def fit_multimappings(
    conn: sqlite3.Connection,
    min_pairs: int,
    min_r2: float,
    max_loo_rmse: float,
    jobs: int | None = None,
) -> list[dict]:
    """Greedy per-target multivariate fits; returns a summary list.

    The targets' feature searches run on ``jobs`` processes (default: every
    core), each reading the database through its own connection.
    """
    versions = [
        dict(r)
        for r in conn.execute(
            "SELECT v.id, v.version, v.harness, b.name AS benchmark,"
            "       b.capability AS capability"
            " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
            " WHERE v.unit = 'fraction'"
        )
    ]
    conn.commit()  # the workers read the committed database
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    # an in-memory database (no path) exists only in this process
    searched = pmap(_search, versions, jobs if path else 1, _init, (path or conn, versions, min_pairs))
    summary = []
    for target, best in zip(versions, searched):
        if best is None or len(best["features"]) < 2:
            # a single-feature multivariate fit adds nothing over the
            # univariate mappings; only store genuinely multi-input fits
            if best is not None and len(best["features"]) < 2:
                summary.append(
                    {
                        "target": _label(target),
                        "method": None,
                        "rejected": "no second source benchmark improved the fit",
                        "n": int(best["metrics"]["n"]),
                        "features": [_label(f) for f in best["features"]],
                    }
                )
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
        if best["metrics"]["R2"] < min_r2 or best["metrics"]["LOO_RMSE"] > max_loo_rmse:
            summary.append(
                {
                    "target": _label(target),
                    "method": None,
                    "rejected": f"R2={best['metrics']['R2']:.2f},"
                    f" LOO RMSE={best['metrics']['LOO_RMSE'] * 100:.1f}pp"
                    " below quality gate",
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
        # replace previous multi-mappings for this target; drop gapfilled
        # rows that referenced them (gapfill will re-fill)
        conn.execute(
            "DELETE FROM scores WHERE source = 'gapfilled' AND multi_mapping_id IN"
            " (SELECT id FROM multi_mappings WHERE to_version_id = ?)",
            (target["id"],),
        )
        conn.execute("DELETE FROM multi_mappings WHERE to_version_id = ?", (target["id"],))
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


def _label(v: sqlite3.Row) -> str:
    return f"{v['benchmark']}/{v['version']}"
