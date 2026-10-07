"""End-to-end pipeline tests on a temporary database."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from benchgap.db import connect, init_db
from benchgap.fit import fit_mappings
from benchgap.gapfill import gapfill
from benchgap.ingest import ingest_csv
from benchgap.report import mapping_summary, render_matrix, score_matrix

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed" / "scores.csv"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_db(c)
    yield c
    c.close()


def _seed_counts():
    n_models = set()
    tb21 = tb40 = 0
    with open(SEED, newline="") as fh:
        for row in csv.DictReader(fh):
            n_models.add(row["model_slug"])
            if row["version"] == "2.1":
                tb21 += 1
            else:
                tb40 += 1
    return len(n_models), tb21, tb40


def test_ingest(conn):
    n = ingest_csv(conn, SEED)
    n_models, tb21, tb40 = _seed_counts()
    assert n == tb21 + tb40
    assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == n_models
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM scores WHERE source = 'measured'"
        ).fetchone()[0]
        == n
    )
    # idempotent
    ingest_csv(conn, SEED)
    assert (
        conn.execute("SELECT COUNT(*) FROM scores WHERE source = 'measured'").fetchone()[0]
        == n
    )


def test_fit_and_gapfill(conn):
    ingest_csv(conn, SEED)
    summary = fit_mappings(conn)
    # both directions have 20 paired models
    assert len(summary) == 2
    by_dir = {(s["from"], s["to"]): s for s in summary}
    fwd = by_dir[("terminal-bench/4.0@artificial-analysis", "terminal-bench/2.1@artificial-analysis")]
    rev = by_dir[("terminal-bench/2.1@artificial-analysis", "terminal-bench/4.0@artificial-analysis")]
    assert fwd["n_pairs"] == 20 and rev["n_pairs"] == 20
    # forward direction (the one gapfill uses on the seed) picks MM+offset;
    # the reverse relationship is convex, so a saturating curve cannot win there
    assert fwd["best_method"] == "mm_offset"
    assert rev["best_method"] in {"linear", "quadratic"}

    filled = gapfill(conn)
    # seed has 20 models measured on both, 14 only on 4.0, 0 only on 2.1;
    # every gapfilled row must come from the forward MM+offset mapping
    n_models, tb21, tb40 = _seed_counts()
    only40 = tb40 - 20
    assert len(filled) == only40
    for f in filled:
        assert 0.0 < f["value"] < 1.0
        assert f["method"] == "mm_offset"

    # every model now has a value on both versions
    n_versions = conn.execute("SELECT COUNT(*) FROM benchmark_versions").fetchone()[0]
    n_models_db = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE source IN ('measured', 'gapfilled')"
        " AND value IS NOT NULL"
    ).fetchone()[0]
    assert total == n_models_db * n_versions

    # gapfill is idempotent
    assert len(gapfill(conn)) == only40


def test_gapfilled_rows_are_traceable(conn):
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
    rows = conn.execute(
        "SELECT s.*, m.method FROM scores s JOIN mappings m ON m.id = s.mapping_id"
        " WHERE s.source = 'gapfilled'"
    ).fetchall()
    assert len(rows) == 14
    for r in rows:
        meta = json.loads(r["prediction_json"])
        assert meta["input_version_id"] is not None
        assert meta["method"] == r["method"]
        assert isinstance(meta["extrapolated"], bool)


def test_report(conn):
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
    summary = mapping_summary(conn)
    assert len(summary) == 2
    methods = {s["method"] for s in summary}
    assert "mm_offset" in methods

    columns, rows = score_matrix(conn)
    assert columns == [
        "terminal-bench/2.1@artificial-analysis",
        "terminal-bench/4.0@artificial-analysis",
    ]
    assert len(rows) == 34
    kinds = {c["kind"] for r in rows for c in r["cells"]}
    assert kinds == {"m", "g"}

    text = render_matrix(conn)
    assert "gapfilled" in text
    assert text.count("*") >= 14


def test_measured_beats_gapfilled(conn):
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
    # insert a fake measured score for a model that was gapfilled
    row = conn.execute(
        "SELECT s.model_id, s.version_id FROM scores s"
        " WHERE s.source = 'gapfilled' LIMIT 1"
    ).fetchone()
    conn.execute(
        "INSERT INTO scores (model_id, version_id, value, source) VALUES (?, ?, 0.5, 'measured')",
        (row["model_id"], row["version_id"]),
    )
    conn.commit()
    _, rows = score_matrix(conn)
    kinds = [c["kind"] for r in rows for c in r["cells"]]
    assert kinds.count("g") == 13
