"""Fit cross-version mappings from paired measured scores in the database."""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .fitting import fit_all, select_best
from .parallel import pmap

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


def _fit_pair(pairs: list[tuple[int, float, float]]):
    return fit_all([p[1] for p in pairs], [p[2] for p in pairs])


def fit_mappings(
    conn: sqlite3.Connection,
    min_pairs: int = MIN_PAIRS,
    keep: Optional[str] = "best",
    jobs: Optional[int] = None,
) -> list[dict]:
    """Fit mappings for every eligible ordered version pair.

    A pair is eligible when both versions use the ``fraction`` unit, belong
    to the same capability, and have at least ``min_pairs`` models measured
    on both. Mappings are never fitted across capabilities: a model missing
    a capability (e.g. a vision benchmark it was never run on) keeps that
    gap rather than inheriting a score from an unrelated benchmark.

    ``keep`` controls what is stored: 'best' keeps only the LOO-CV-selected
    mapping per pair, 'all' keeps every candidate fit. The pairs are fitted on
    ``jobs`` processes (default: every core). Returns a summary list.
    """
    versions = [v for v in _versions(conn) if v["unit"] == "fraction"]
    tasks = []
    for src in versions:
        for dst in versions:
            if src["id"] == dst["id"] or src["capability"] != dst["capability"]:
                continue
            pairs = _paired_scores(conn, src["id"], dst["id"])
            if len(pairs) >= min_pairs:
                tasks.append((src, dst, pairs))
    fitted = pmap(_fit_pair, [t[2] for t in tasks], jobs)

    summary = []
    for (src, dst, pairs), results in zip(tasks, fitted):
        best = select_best(results)
        if best is None:  # no candidate converged
            continue
        if best.metrics["R2"] < MIN_R2 or (
            best.metrics["LOO_RMSE"] == best.metrics["LOO_RMSE"]
            and best.metrics["LOO_RMSE"] > MAX_LOO_RMSE
        ):
            summary.append(
                {
                    "from": f"{src['benchmark']}/{src['version']}@{src['harness']}",
                    "to": f"{dst['benchmark']}/{dst['version']}@{dst['harness']}",
                    "capability": src["capability"],
                    "n_pairs": len(pairs),
                    "best_method": None,
                    "best_LOO_RMSE": best.metrics["LOO_RMSE"],
                    "rejected": f"R2={best.metrics['R2']:.2f},"
                    f" LOO RMSE={best.metrics['LOO_RMSE'] * 100:.1f}pp"
                    " below quality gate",
                    "candidates": {
                        r.method: {
                            "R2": r.metrics["R2"],
                            "LOO_RMSE": r.metrics["LOO_RMSE"],
                        }
                        for r in results
                    },
                }
            )
            continue
        stored = results if keep == "all" else [best]
        for r in stored:
            _store_mapping(conn, src["id"], dst["id"], r, pairs)
        summary.append(
            {
                "from": f"{src['benchmark']}/{src['version']}@{src['harness']}",
                "to": f"{dst['benchmark']}/{dst['version']}@{dst['harness']}",
                "capability": src["capability"],
                "n_pairs": len(pairs),
                "best_method": best.method,
                "best_LOO_RMSE": best.metrics["LOO_RMSE"],
                "candidates": {
                    r.method: {
                        "R2": r.metrics["R2"],
                        "LOO_RMSE": r.metrics["LOO_RMSE"],
                    }
                    for r in results
                },
            }
        )
    conn.commit()
    return summary
