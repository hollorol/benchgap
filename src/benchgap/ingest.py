"""Ingest measured scores from the canonical long-format seed CSV."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .db import get_or_create_model, get_or_create_version, set_measured_score


def ingest_csv(conn: sqlite3.Connection, path: str | Path) -> int:
    """Load seed CSV rows into the database. Returns the number of scores loaded.

    Expected columns: model_slug, model_name, release, benchmark, version,
    harness, score, source_url, retrieved_at
    """
    n = 0
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            model_id = get_or_create_model(
                conn, row["model_slug"], row["model_name"], row.get("release") or None
            )
            version_id = get_or_create_version(
                conn,
                row["benchmark"],
                row["version"],
                row["harness"],
                row.get("source_url") or None,
            )
            set_measured_score(
                conn,
                model_id,
                version_id,
                float(row["score"]),
                row.get("retrieved_at") or None,
            )
            n += 1
    conn.commit()
    return n
