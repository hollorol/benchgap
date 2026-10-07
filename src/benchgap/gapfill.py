"""Gapfill: predict missing scores from measured scores via fitted mappings."""
from __future__ import annotations

import json
import sqlite3

from .fitting import predict
from .multivariate import predict_mv


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


def _multi_by_target(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    """Best multivariate mapping per target (lowest LOO_RMSE)."""
    rows = conn.execute(
        "SELECT * FROM multi_mappings"
        " ORDER BY to_version_id,"
        "   COALESCE(json_extract(metrics_json, '$.LOO_RMSE'), 1e9)"
    ).fetchall()
    by_target: dict[int, sqlite3.Row] = {}
    for r in rows:
        by_target.setdefault(r["to_version_id"], r)
    return by_target


def _measured(conn: sqlite3.Connection, model_id: int, version_id: int):
    return conn.execute(
        "SELECT value FROM scores"
        " WHERE model_id = ? AND version_id = ? AND source = 'measured'",
        (model_id, version_id),
    ).fetchone()


def _loo_key(metrics: dict) -> tuple:
    loo = metrics.get("LOO_RMSE")
    rmse = metrics.get("RMSE")
    loo = loo if loo is not None and loo == loo else float("inf")
    rmse = rmse if rmse is not None and rmse == rmse else float("inf")
    return (loo, rmse)


def _multi_prediction(conn, model, mm) -> dict | None:
    """Multivariate candidate for a model, or None if a feature is missing."""
    fids = json.loads(mm["feature_version_ids_json"])
    xs = []
    for fid in fids:
        row = _measured(conn, model["id"], fid)
        if row is None:
            return None
        xs.append(row["value"])
    params = json.loads(mm["params_json"])
    value = float(predict_mv(mm["method"], params, [xs])[0])
    ranges = json.loads(mm["train_ranges_json"]) if mm["train_ranges_json"] else {}
    extrapolated = any(
        fid in ranges and not (ranges[fid][0] <= x <= ranges[fid][1])
        for fid, x in zip(fids, xs)
    )
    metrics = json.loads(mm["metrics_json"])
    return {
        "value": value,
        "metrics": metrics,
        "prediction_json": json.dumps(
            {
                "kind": "multi",
                "method": mm["method"],
                "input_scores": {str(fid): x for fid, x in zip(fids, xs)},
                "extrapolated": extrapolated,
            }
        ),
        "multi_mapping_id": mm["id"],
        "label": f"{mm['method']} (multi)",
    }


def _uni_prediction(conn, model, mp) -> dict | None:
    row = _measured(conn, model["id"], mp["from_version_id"])
    if row is None:
        return None
    params = json.loads(mp["params_json"])
    metrics = json.loads(mp["metrics_json"])
    value = float(predict(mp["method"], params, [row["value"]])[0])
    train_range = json.loads(mp["train_range_json"]) if mp["train_range_json"] else None
    extrapolated = train_range is not None and not (
        train_range["x_min"] <= row["value"] <= train_range["x_max"]
    )
    return {
        "value": value,
        "metrics": metrics,
        "prediction_json": json.dumps(
            {
                "kind": "uni",
                "input_version_id": mp["from_version_id"],
                "input_score": row["value"],
                "method": mp["method"],
                "extrapolated": extrapolated,
            }
        ),
        "mapping_id": mp["id"],
        "label": mp["method"],
    }


def gapfill(conn: sqlite3.Connection) -> list[dict]:
    """Fill missing measured scores with mapping predictions.

    For every model lacking a measured score on a target version, the best
    available predictor wins: multivariate mappings (which need the model
    to be measured on every feature version) compete with univariate
    mappings by LOO-CV RMSE. Prior gapfilled rows for the same
    (model, version) are replaced, so re-running after a refit refreshes
    predictions. Returns a summary of the filled rows.
    """
    by_target = _mappings_by_target(conn)
    multi_by_target = _multi_by_target(conn)
    labels = {
        r["id"]: f"{r['benchmark']}/{r['version']}"
        for r in conn.execute(
            "SELECT v.id, v.version, b.name AS benchmark"
            " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        )
    }
    filled = []
    targets = sorted(set(by_target) | set(multi_by_target))
    for target_id in targets:
        missing = conn.execute(
            "SELECT m.id, m.slug FROM models m"
            " WHERE NOT EXISTS (SELECT 1 FROM scores s"
            "   WHERE s.model_id = m.id AND s.version_id = ? AND s.source = 'measured')",
            (target_id,),
        ).fetchall()
        for model in missing:
            candidates = []
            mm = multi_by_target.get(target_id)
            if mm is not None:
                cand = _multi_prediction(conn, model, mm)
                if cand is not None:
                    candidates.append(cand)
            for mp in by_target.get(target_id, []):
                cand = _uni_prediction(conn, model, mp)
                if cand is not None:
                    candidates.append(cand)
            if not candidates:
                continue
            best = min(candidates, key=lambda c: _loo_key(c["metrics"]))
            conn.execute(
                "DELETE FROM scores WHERE model_id = ? AND version_id = ?"
                " AND source = 'gapfilled'",
                (model["id"], target_id),
            )
            conn.execute(
                "INSERT INTO scores (model_id, version_id, value, source, mapping_id,"
                " multi_mapping_id, prediction_json)"
                " VALUES (?, ?, ?, 'gapfilled', ?, ?, ?)",
                (
                    model["id"],
                    target_id,
                    best["value"],
                    best.get("mapping_id"),
                    best.get("multi_mapping_id"),
                    best["prediction_json"],
                ),
            )
            filled.append(
                {
                    "model_id": model["id"],
                    "slug": model["slug"],
                    "target_version_id": target_id,
                    "target": labels[target_id],
                    "value": best["value"],
                    "method": best["label"],
                    "kind": json.loads(best["prediction_json"])["kind"],
                    "extrapolated": json.loads(best["prediction_json"])["extrapolated"],
                }
            )
    conn.commit()
    return filled
