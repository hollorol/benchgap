"""Fit cross-version mappings from paired measured scores in the database."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Optional

from .cache import FitCache, fit_key
from .db import version_label
from .fitting import FitResult, fit_all, select_best
from .parallel import ipmap

# Minimum number of paired models required to fit a mapping.
MIN_PAIRS = 5
# Quality gate: the selected fit must explain at least this much variance and
# cross-validate better than this RMSE, otherwise the pair keeps no mapping
# (and models missing that benchmark keep the gap - a weak mapping would
# fabricate scores rather than calibrate them).
MIN_R2 = 0.3
MAX_LOO_RMSE = 0.15


def _versions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT v.id, v.version, v.harness, v.unit, b.name AS benchmark,"
        "       b.capability AS capability"
        " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        " ORDER BY b.name, v.version, v.harness"
    ).fetchall()


def _paired_scores(
    conn: sqlite3.Connection, from_id: int, to_id: int
) -> list[tuple[int, float, float]]:
    """(model_id, x, y) for models measured on both versions."""
    rows = conn.execute(
        "SELECT sx.model_id, sx.value AS x, sy.value AS y"
        " FROM scores sx"
        " JOIN scores sy ON sy.model_id = sx.model_id AND sy.source = 'measured'"
        " WHERE sx.version_id = ? AND sx.source = 'measured' AND sy.version_id = ?",
        (from_id, to_id),
    ).fetchall()
    return [(r["model_id"], r["x"], r["y"]) for r in rows]


def _store_mapping(
    conn: sqlite3.Connection,
    from_id: int,
    to_id: int,
    result,
    pairs: list[tuple[int, float, float]],
) -> int:
    xs = [p[1] for p in pairs]
    train_range = {"x_min": min(xs), "x_max": max(xs)}
    conn.execute(
        "INSERT INTO mappings (from_version_id, to_version_id, method, params_json,"
        " metrics_json, n_points, train_range_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (from_version_id, to_version_id, method) DO UPDATE SET"
        " params_json = excluded.params_json, metrics_json = excluded.metrics_json,"
        " n_points = excluded.n_points, train_range_json = excluded.train_range_json,"
        " created_at = datetime('now')",
        (
            from_id,
            to_id,
            result.method,
            json.dumps(result.params),
            json.dumps(result.metrics),
            len(pairs),
            json.dumps(train_range),
        ),
    )
    mapping_id = conn.execute(
        "SELECT id FROM mappings"
        " WHERE from_version_id = ? AND to_version_id = ? AND method = ?",
        (from_id, to_id, result.method),
    ).fetchone()["id"]
    conn.execute("DELETE FROM mapping_points WHERE mapping_id = ?", (mapping_id,))
    conn.executemany(
        "INSERT INTO mapping_points (mapping_id, model_id, x, y) VALUES (?, ?, ?, ?)",
        [(mapping_id, m, x, y) for m, x, y in pairs],
    )
    return mapping_id


def _xy(pairs: list[tuple[int, float, float]]) -> tuple[list[float], list[float]]:
    return [p[1] for p in pairs], [p[2] for p in pairs]


def _fit_pair(pairs: list[tuple[int, float, float]]):
    return fit_all(*_xy(pairs))


# fits between two saves of the fit cache, so a run cut off midway keeps most of its fits
CHECKPOINT = 500


def _pair_tasks(conn: sqlite3.Connection, min_pairs: int, cross: bool) -> list[tuple]:
    """(source, target, paired scores) for the ordered pairs of fraction versions with at least
    min_pairs models measured on both: of the same capability, or (cross) of different ones."""
    versions = [v for v in _versions(conn) if v["unit"] == "fraction"]
    tasks = []
    for src in versions:
        for dst in versions:
            if src["id"] == dst["id"] or (src["capability"] != dst["capability"]) != cross:
                continue
            pairs = _paired_scores(conn, src["id"], dst["id"])
            if len(pairs) >= min_pairs:
                tasks.append((src, dst, pairs))
    return tasks


def _selected(tasks: list[tuple], jobs: Optional[int], cache: FitCache):
    """(source, target, pairs, every candidate fit, the selected one, why it fails the quality
    gate or None) per task whose fits converged; the fits come from the cache or are fitted in
    parallel, the new ones saved every CHECKPOINT fits."""
    keys = [fit_key(*_xy(t[2])) for t in tasks]
    todo = {k: t[2] for k, t in zip(keys, tasks) if k not in cache}
    # the results first, so the process pool is shut down as the last one comes in
    for i, (results, k) in enumerate(zip(ipmap(_fit_pair, todo.values(), jobs), todo), 1):
        cache.put(k, [asdict(r) for r in results])
        if i % CHECKPOINT == 0:
            cache.checkpoint()
    for (src, dst, pairs), k in zip(tasks, keys):
        results = [FitResult(**r) for r in cache.get(k, None)]
        best = select_best(results)
        if best is not None:  # some candidate converged
            yield src, dst, pairs, results, best, _rejected(best)


def gate_failure(metrics: dict, min_r2: float = MIN_R2, max_loo_rmse: float = MAX_LOO_RMSE) -> Optional[str]:
    """Why a fit with these metrics fails the quality gate; None if it passes."""
    loo = metrics["LOO_RMSE"]
    if metrics["R2"] < min_r2 or (loo == loo and loo > max_loo_rmse):
        return f"R2={metrics['R2']:.2f}, LOO RMSE={loo * 100:.1f}pp below quality gate"
    return None


def _rejected(best: FitResult) -> Optional[str]:
    """Why the selected fit fails the quality gate; None if it passes."""
    return gate_failure(best.metrics)


def _summary(src, dst, pairs, best, rejected) -> dict:
    return {"from": version_label(src), "to": version_label(dst), "n_pairs": len(pairs),
            "best_LOO_RMSE": best.metrics["LOO_RMSE"], "rejected": rejected}


def fit_mappings(
    conn: sqlite3.Connection,
    min_pairs: int = MIN_PAIRS,
    keep: Optional[str] = "best",
    jobs: Optional[int] = None,
    cache: Optional[FitCache] = None,
) -> list[dict]:
    """Fit mappings for every eligible ordered version pair.

    A pair is eligible when both versions use the ``fraction`` unit, belong
    to the same capability, and have at least ``min_pairs`` models measured
    on both. Mappings are never fitted across capabilities: a model missing
    a capability (e.g. a vision benchmark it was never run on) keeps that
    gap rather than inheriting a score from an unrelated benchmark (the
    cross-domain view has its own fits: fit_cross_mappings).

    ``keep`` controls what is stored: 'best' keeps only the LOO-CV-selected
    mapping per pair, 'all' keeps every candidate fit. The pairs are fitted on
    ``jobs`` processes (default: every core); a pair whose paired scores are
    in ``cache`` reuses its earlier fits. Returns a summary list.
    """
    summary = []
    for src, dst, pairs, results, best, rejected in _selected(
        _pair_tasks(conn, min_pairs, cross=False), jobs, cache or FitCache(None, "fit")
    ):
        summary.append({
            **_summary(src, dst, pairs, best, rejected),
            "capability": src["capability"],
            "best_method": None if rejected else best.method,
            "candidates": {
                r.method: {"R2": r.metrics["R2"], "LOO_RMSE": r.metrics["LOO_RMSE"]}
                for r in results
            },
        })
        if not rejected:
            for r in results if keep == "all" else [best]:
                _store_mapping(conn, src["id"], dst["id"], r, pairs)
    conn.commit()
    return summary


def fit_cross_mappings(
    conn: sqlite3.Connection,
    min_pairs: int = MIN_PAIRS,
    jobs: Optional[int] = None,
    cache: Optional[FitCache] = None,
) -> list[dict]:
    """Fit every eligible ordered pair of versions of different capabilities, for the
    cross-domain view only: how well one domain's benchmarks predict another's.

    The fits are those of fit_mappings, and each pair's selected fit is stored in
    cross_mappings with whether it passes the quality gate (``passes``). No estimate
    comes from them: gapfill uses only the mappings table. Replaces earlier cross
    mappings. Returns a summary list, as fit_mappings' without the candidates.
    """
    conn.execute("DELETE FROM cross_mappings")
    summary = []
    for src, dst, pairs, _, best, rejected in _selected(
        _pair_tasks(conn, min_pairs, cross=True), jobs, cache or FitCache(None, "crossfit")
    ):
        conn.execute(
            "INSERT INTO cross_mappings (from_version_id, to_version_id, method, params_json,"
            " metrics_json, n_points, passes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (src["id"], dst["id"], best.method, json.dumps(best.params),
             json.dumps(best.metrics), len(pairs), int(rejected is None)),
        )
        summary.append(_summary(src, dst, pairs, best, rejected))
    conn.commit()
    return summary
