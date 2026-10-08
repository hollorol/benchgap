"""Shared fixtures.

The tests run on tests/data/seed.csv, a fixed subset of the BenchLM seed (20
of its benchmarks, 206 models), not on data/seed/scores.csv: the real seed
changes every day and takes minutes to fit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchgap.db import connect, init_db
from benchgap.fit import MAX_LOO_RMSE, MIN_PAIRS, MIN_R2, fit_cross_mappings, fit_mappings
from benchgap.gapfill import gapfill
from benchgap.ingest import ingest_csv
from benchgap.multivariate import fit_cross_multimappings, fit_multimappings

SEED = Path(__file__).resolve().parent / "data" / "seed.csv"
CROSS_MIN_PAIRS = 120


@pytest.fixture(scope="session")
def build(tmp_path_factory):
    """The test seed, fitted (univariate, multivariate and across capabilities) and gapfilled
    once per test run.

    A dict: conn (the database), fit, multifit, crossfit and crossmultifit (the fits' summaries).
    """
    conn = connect(tmp_path_factory.mktemp("seed") / "seed.db")
    init_db(conn)
    ingest_csv(conn, SEED)
    out = {"conn": conn, "fit": fit_mappings(conn)}
    out["multifit"] = fit_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    # only the cross pairs with many shared models: all of them take a minute on 2 cores
    out["crossfit"] = fit_cross_mappings(conn, min_pairs=CROSS_MIN_PAIRS)
    out["crossmultifit"] = fit_cross_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    gapfill(conn)
    yield out
    conn.close()


@pytest.fixture(scope="session")
def gapfilled_db(build):
    """The built database. Read-only: tests that write use writable_db."""
    return build["conn"]


@pytest.fixture()
def writable_db(gapfilled_db, tmp_path):
    """A copy of the built database to change."""
    conn = connect(tmp_path / "copy.db")
    gapfilled_db.backup(conn)
    yield conn
    conn.close()
