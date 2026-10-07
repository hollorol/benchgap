"""Shared fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchgap.db import connect, init_db
from benchgap.fit import fit_mappings
from benchgap.gapfill import gapfill
from benchgap.ingest import ingest_csv

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed" / "scores.csv"


@pytest.fixture(scope="session")
def gapfilled_db(tmp_path_factory):
    """The seed, fitted and gapfilled once per test run (fitting is slow).

    Read-only: tests that write to the database build their own.
    """
    conn = connect(tmp_path_factory.mktemp("seed") / "seed.db")
    init_db(conn)
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
    yield conn
    conn.close()
