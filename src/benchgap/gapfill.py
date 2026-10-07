"""Gapfill: predict missing scores from measured scores via fitted mappings."""
from __future__ import annotations

import json
import sqlite3

from .fitting import predict


def _mappings_by_target(conn: sqlite3.Connection) -> dict[int, list[sqlite3.Row]]:
    """Best mapping per (source version -> target version), keyed by target.

    When multiple methods are stored for the same pair, the one with the
    lowest LOO_RMSE wins; deterministic fits tie-break on RMSE.
    """
    rows = conn.execute(
        "SELECT m.* FROM mappings m"
        " ORDER BY m.to_version_id,"
        "   COALESCE(json_extract(m.metrics_json, '$.LOO_RMSE'), 1e9),"
        "   COALESCE(json_extract(m.metrics_json, '$.RMSE'), 1e9)"
    ).fetchall()
    best: dict[tuple[int, int], sqlite3.Row] = {}
    for r in rows:
        key = (r["from_version_id"], r["to_version_id"])
        best.setdefault(key, r)
    by_target: dict[int, list[sqlite3.Row]] = {}
    for (src, dst), r in best.items():
        by_target.setdefault(dst, []).append(r)
    return by_target


def gapfill(conn: sqlite3.Connection) -> list[dict]:
    """Fill missing measured scores with mapping predictions.

    For every model lacking a measured score on a target version, every
    source version the model *is* measured on is considered; the mapping
    with the lowest LOO_RMSE wins. Prior gapfilled rows for the same
    (model, version) are replaced, so re-running after a refit refreshes
    predictions. Returns a summary of the filled rows.
    """
    by_target = _mappings_by_target(conn)
    labels = {
        r["id"]: f"{r['benchmark']}/{r['version']}"
        for r in conn.execute(
            "SELECT v.id, v.version, b.name AS benchmark"
            " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        )
    }
    filled = []
    for target_id, mappings in by_target.items():
        missing = conn.execute(
            "SELECT m.id, m.slug FROM models m"
            " WHERE NOT EXISTS (SELECT 1 FROM scores s"
            "   WHERE s.model_id = m.id AND s.version_id = ? AND s.source = 'measured')",
            (target_id,),
        ).fetchall()
        for model in missing:
            best = None
            for mp in mappings:
                row = conn.execute(
                    "SELECT value FROM scores"
                    " WHERE model_id = ? AND version_id = ? AND source = 'measured'",
                    (model["id"], mp["from_version_id"]),
                ).fetchone()
                if row is None:
                    continue
                metrics = json.loads(mp["metrics_json"])
                cand = {
                    "mapping": mp,
                    "x": row["value"],
                    "loo": metrics.get("LOO_RMSE"),
                    "rmse": metrics.get("RMSE"),
                }
                if best is None or _cand_key(cand) < _cand_key(best):
                    best = cand
            if best is None:
                continue
            mp = best["mapping"]
            params = json.loads(mp["params_json"])
            value = float(predict(mp["method"], params, [best["x"]])[0])
            train_range = json.loads(mp["train_range_json"]) if mp["train_range_json"] else None
            extrapolated = train_range is not None and not (
                train_range["x_min"] <= best["x"] <= train_range["x_max"]
            )
            conn.execute(
                "DELETE FROM scores WHERE model_id = ? AND version_id = ?"
                " AND source = 'gapfilled'",
                (model["id"], target_id),
            )
            conn.execute(
                "INSERT INTO scores (model_id, version_id, value, source, mapping_id,"
                " prediction_json)"
                " VALUES (?, ?, ?, 'gapfilled', ?, ?)",
                (
                    model["id"],
                    target_id,
                    value,
                    mp["id"],
                    json.dumps(
                        {
                            "input_version_id": mp["from_version_id"],
                            "input_score": best["x"],
                            "method": mp["method"],
                            "extrapolated": extrapolated,
                        }
                    ),
                ),
            )
            filled.append(
                {
                    "model_id": model["id"],
                    "slug": model["slug"],
                    "target_version_id": target_id,
                    "target": labels[target_id],
                    "value": value,
                    "method": mp["method"],
                    "extrapolated": extrapolated,
                }
            )
    conn.commit()
    return filled


def _cand_key(cand: dict) -> tuple:
    loo = cand["loo"]
    rmse = cand["rmse"]
    loo = loo if loo is not None and loo == loo else float("inf")
    rmse = rmse if rmse is not None and rmse == rmse else float("inf")
    return (loo, rmse)
