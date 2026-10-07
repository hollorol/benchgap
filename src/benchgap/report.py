"""Human-readable views of the database: mapping summary and score matrix."""
from __future__ import annotations

import sqlite3


def _version_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT v.id, v.version, v.harness, b.name AS benchmark"
        " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        " ORDER BY b.name, v.version, v.harness"
    ).fetchall()


def _labels(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        r["id"]: f"{r['benchmark']}/{r['version']}" + (
            f"@{r['harness']}" if r["harness"] != "unknown" else ""
        )
        for r in _version_rows(conn)
    }


def mapping_summary(conn: sqlite3.Connection) -> list[dict]:
    """One row per version pair: the best mapping and its quality."""
    labels = _labels(conn)
    rows = conn.execute(
        "SELECT m.from_version_id, m.to_version_id, m.method, m.n_points,"
        "       json_extract(m.metrics_json, '$.R2') AS r2,"
        "       json_extract(m.metrics_json, '$.LOO_RMSE') AS loo_rmse,"
        "       (SELECT COUNT(*) FROM mappings m2"
        "          WHERE m2.from_version_id = m.from_version_id"
        "            AND m2.to_version_id = m.to_version_id) AS n_candidates"
        " FROM mappings m"
        " WHERE m.id = ("
        "   SELECT m2.id FROM mappings m2"
        "    WHERE m2.from_version_id = m.from_version_id"
        "      AND m2.to_version_id = m.to_version_id"
        "    ORDER BY COALESCE(json_extract(m2.metrics_json, '$.LOO_RMSE'), 1e9),"
        "             COALESCE(json_extract(m2.metrics_json, '$.RMSE'), 1e9)"
        "    LIMIT 1)"
        " ORDER BY m.from_version_id, m.to_version_id"
    ).fetchall()
    return [
        {
            "from": labels[r["from_version_id"]],
            "to": labels[r["to_version_id"]],
            "method": r["method"],
            "n_pairs": r["n_points"],
            "candidates": r["n_candidates"],
            "R2": r["r2"],
            "LOO_RMSE_pp": r["loo_rmse"] * 100 if r["loo_rmse"] is not None else None,
        }
        for r in rows
    ]


def score_matrix(conn: sqlite3.Connection) -> tuple[list[str], list[dict]]:
    """Models x versions matrix in percent; gapfilled cells marked 'g'."""
    versions = _version_rows(conn)
    version_ids = [v["id"] for v in versions]
    version_labels = _labels(conn)
    columns = [version_labels[vid] for vid in version_ids]

    # (model_id, version_id) -> (value, source); measured wins over gapfilled
    scores: dict[tuple[int, int], tuple[float, str]] = {}
    for r in conn.execute("SELECT model_id, version_id, value, source FROM scores"):
        key = (r["model_id"], r["version_id"])
        if r["source"] == "measured" or key not in scores:
            scores[key] = (r["value"], r["source"])

    rows = []
    for m in conn.execute("SELECT id, slug, name FROM models ORDER BY slug"):
        cells = []
        for vid in version_ids:
            entry = scores.get((m["id"], vid))
            if entry is None:
                cells.append({"value": None, "kind": "missing"})
            else:
                cells.append(
                    {
                        "value": entry[0] * 100,
                        "kind": "g" if entry[1] == "gapfilled" else "m",
                    }
                )
        rows.append({"slug": m["slug"], "name": m["name"], "cells": cells})
    return columns, rows


def render_matrix(conn: sqlite3.Connection) -> str:
    """Plain-text matrix: measured values plain, gapfilled marked with *."""
    columns, rows = score_matrix(conn)
    name_w = max([len(r["slug"]) for r in rows] + [len("model")])
    col_w = max([len(c) for c in columns] + [8])
    lines = [" " * name_w + "  " + "  ".join(c.ljust(col_w) for c in columns)]
    lines.append("-" * len(lines[0]))
    for r in rows:
        cells = []
        for c in r["cells"]:
            if c["value"] is None:
                cells.append("-".ljust(col_w))
            else:
                mark = "*" if c["kind"] == "g" else ""
                cells.append(f"{c['value']:5.1f}{mark}".ljust(col_w))
        lines.append(r["slug"].ljust(name_w) + "  " + "  ".join(cells))
    lines.append("")
    lines.append("* = gapfilled (fitted mapping, not a measured score)")
    return "\n".join(lines)
