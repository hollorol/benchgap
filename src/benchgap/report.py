"""Human-readable views of the database: mapping summary and score matrix."""
from __future__ import annotations

import json
import sqlite3

# Display order for capability groups in the matrix.
CAPABILITY_ORDER = [
    "agentic-terminal",
    "agentic-tool",
    "coding",
    "math",
    "knowledge",
    "instruction-following",
    "vision",
    "long-context",
    "general",
]


def _version_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT v.id, v.version, v.harness, v.unit, b.name AS benchmark,"
        "       b.capability AS capability"
        " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
    ).fetchall()
    cap_rank = {c: i for i, c in enumerate(CAPABILITY_ORDER)}
    return sorted(
        rows,
        key=lambda r: (cap_rank.get(r["capability"], 99), r["benchmark"], r["version"]),
    )


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


def multi_mapping_summary(conn: sqlite3.Connection) -> list[dict]:
    """One row per multivariate mapping: target, features, quality."""
    labels = _labels(conn)
    rows = conn.execute(
        "SELECT * FROM multi_mappings"
        " ORDER BY COALESCE(json_extract(metrics_json, '$.LOO_RMSE'), 1e9)"
    ).fetchall()
    return [
        {
            "target": labels[r["to_version_id"]],
            "method": r["method"],
            "features": [labels[fid] for fid in json.loads(r["feature_version_ids_json"])],
            "n_pairs": r["n_points"],
            "R2": json.loads(r["metrics_json"])["R2"],
            "LOO_RMSE_pp": json.loads(r["metrics_json"])["LOO_RMSE"] * 100,
        }
        for r in rows
    ]


def score_matrix(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Models x versions matrix; gapfilled cells marked 'g', missing '.'.

    Columns are grouped by capability. Cell values are in the version's
    native unit (fractions rendered as percent by callers).
    """
    versions = _version_rows(conn)
    columns = []
    for v in versions:
        columns.append(
            {
                "id": v["id"],
                "label": f"{v['benchmark']}/{v['version']}",
                "capability": v["capability"],
                "unit": v["unit"],
            }
        )

    # (model_id, version_id) -> (value, source); measured wins over gapfilled
    scores: dict[tuple[int, int], tuple[float, str]] = {}
    for r in conn.execute("SELECT model_id, version_id, value, source FROM scores"):
        key = (r["model_id"], r["version_id"])
        if r["source"] == "measured" or key not in scores:
            scores[key] = (r["value"], r["source"])

    rows = []
    for m in conn.execute("SELECT id, slug, name FROM models ORDER BY slug"):
        cells = []
        for col in columns:
            entry = scores.get((m["id"], col["id"]))
            if entry is None:
                cells.append({"value": None, "kind": "missing"})
            else:
                cells.append(
                    {
                        "value": entry[0],
                        "kind": "g" if entry[1] == "gapfilled" else "m",
                    }
                )
        rows.append({"id": m["id"], "slug": m["slug"], "name": m["name"], "cells": cells})
    return columns, rows


def _format_cell(col: dict, cell: dict) -> str:
    if cell["value"] is None:
        return "-".ljust(8)
    mark = "*" if cell["kind"] == "g" else ""
    if col["unit"] == "fraction":
        return f"{cell['value'] * 100:5.1f}{mark}".ljust(8)
    return f"{cell['value']:5.1f}{mark}".ljust(8)


def render_matrix(conn: sqlite3.Connection) -> str:
    """Plain-text matrix: measured values plain, gapfilled marked with *."""
    columns, rows = score_matrix(conn)
    name_w = max([len(r["slug"]) for r in rows] + [len("model")])
    lines = []
    header_cells = []
    last_cap = None
    for col in columns:
        cap_mark = col["capability"] if col["capability"] != last_cap else ""
        last_cap = col["capability"]
        header_cells.append(f"{cap_mark:>8.8}")
    # capability header line + column header line
    lines.append(" " * (name_w + 2) + " ".join(header_cells))
    lines.append(
        " " * (name_w + 2) + " ".join(c["label"][:8].ljust(8) for c in columns)
    )
    lines.append("-" * len(lines[-1]))
    for r in rows:
        cells = " ".join(
            _format_cell(c, cell) for c, cell in zip(columns, r["cells"])
        )
        lines.append(r["slug"].ljust(name_w) + "  " + cells)
    lines.append("")
    lines.append("* = gapfilled (fitted mapping, not a measured score)")
    return "\n".join(lines)
