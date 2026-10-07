"""End-to-end pipeline tests on a temporary database (big multi-benchmark seed)."""
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

# Snapshot expectations for the current seed (regenerate if the seed changes).
N_MODELS = 85
N_VERSIONS = 24
N_MEASURED = 396
N_TB_PAIRS = 15


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_db(c)
    yield c
    c.close()


def test_ingest(conn):
    n = ingest_csv(conn, SEED)
    assert n == N_MEASURED
    assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == N_MODELS
    assert (
        conn.execute("SELECT COUNT(*) FROM benchmark_versions").fetchone()[0] == N_VERSIONS
    )
    # idempotent
    ingest_csv(conn, SEED)
    assert (
        conn.execute("SELECT COUNT(*) FROM scores WHERE source = 'measured'").fetchone()[0]
        == N_MEASURED
    )


def test_capabilities_stored(conn):
    ingest_csv(conn, SEED)
    caps = {
        r["capability"]
        for r in conn.execute("SELECT DISTINCT capability FROM benchmarks")
    }
    assert "vision" in caps
    assert "agentic-terminal" in caps


def test_fit_within_capability_and_quality_gate(conn):
    ingest_csv(conn, SEED)
    summary = fit_mappings(conn)

    stored = [s for s in summary if s["best_method"] is not None]
    rejected = [s for s in summary if s["best_method"] is None]
    assert stored, "expected several mappings to pass the gate"
    assert rejected, "expected some weak pairs to be rejected by the quality gate"

    # the flagship Terminal-Bench mapping is stored in both directions
    by_dir = {(s["from"], s["to"]): s for s in stored}
    fwd = by_dir[("terminal-bench/4.0@artificial-analysis", "terminal-bench/2.1@artificial-analysis")]
    rev = by_dir[("terminal-bench/2.1@artificial-analysis", "terminal-bench/4.0@artificial-analysis")]
    assert fwd["n_pairs"] == N_TB_PAIRS
    assert fwd["best_method"] == "mm_offset"
    assert rev["best_method"] == "mm_offset_inv"

    # no stored mapping crosses a capability boundary
    cross = conn.execute(
        "SELECT COUNT(*) FROM mappings m"
        " JOIN benchmark_versions vf ON vf.id = m.from_version_id"
        " JOIN benchmark_versions vt ON vt.id = m.to_version_id"
        " JOIN benchmarks bf ON bf.id = vf.benchmark_id"
        " JOIN benchmarks bt ON bt.id = vt.benchmark_id"
        " WHERE bf.capability != bt.capability"
    ).fetchone()[0]
    assert cross == 0


def test_gapfill_keeps_capability_gaps(conn):
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    filled = gapfill(conn)
    assert len(filled) > 100
    for f in filled:
        # predictions are clamped into [0, 1]; the inverse MM form clamps
        # models scoring at/below the fitted baseline to exactly 0
        assert 0.0 <= f["value"] <= 1.0

    # every gapfilled row is traceable and same-capability
    rows = conn.execute(
        "SELECT s.*, m.method, bt.capability AS to_cap, bf.capability AS from_cap"
        " FROM scores s"
        " JOIN mappings m ON m.id = s.mapping_id"
        " JOIN benchmark_versions vf ON vf.id = m.from_version_id"
        " JOIN benchmark_versions vt ON vt.id = s.version_id"
        " JOIN benchmarks bf ON bf.id = vf.benchmark_id"
        " JOIN benchmarks bt ON bt.id = vt.benchmark_id"
        " WHERE s.source = 'gapfilled'"
    ).fetchall()
    assert len(rows) == len(filled)
    for r in rows:
        meta = json.loads(r["prediction_json"])
        assert meta["method"] == r["method"]
        assert isinstance(meta["extrapolated"], bool)
        assert r["to_cap"] == r["from_cap"], "gapfilled across capabilities"

    # gaps are kept: models measured on few benchmarks do not get every cell
    # filled - e.g. models measured only on terminal-bench-hard
    kept = conn.execute(
        "SELECT m.slug FROM models m"
        " WHERE (SELECT COUNT(*) FROM scores s WHERE s.model_id = m.id"
        "        AND s.source = 'measured') = 1"
        "   AND (SELECT COUNT(*) FROM scores s WHERE s.model_id = m.id"
        "        AND s.source = 'gapfilled') = 0"
    ).fetchall()
    assert len(kept) >= 5, "capability gaps must stay gaps"

    # gapfill is idempotent
    assert len(gapfill(conn)) == len(filled)


def test_report(conn):
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
    summary = mapping_summary(conn)
    assert any(s["method"] == "mm_offset" for s in summary)
    assert any(s["method"] == "mm_offset_inv" for s in summary)

    columns, rows = score_matrix(conn)
    assert len(columns) == N_VERSIONS
    assert len(rows) == N_MODELS
    kinds = {c["kind"] for r in rows for c in r["cells"]}
    assert kinds == {"m", "g", "missing"}

    text = render_matrix(conn)
    assert "gapfilled" in text


def test_measured_beats_gapfilled(conn):
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
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
    n_gap = sum(1 for r in rows for c in r["cells"] if c["kind"] == "g")
    before = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE source = 'gapfilled'"
    ).fetchone()[0]
    assert n_gap == before - 1
