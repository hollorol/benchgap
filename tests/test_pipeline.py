"""End-to-end pipeline tests on the test seed (tests/data/seed.csv)."""
from __future__ import annotations

import json

import pytest

from benchgap.cache import FitCache
from benchgap.db import connect, init_db
from benchgap.fit import fit_cross_mappings
from benchgap.gapfill import gapfill
from benchgap.ingest import ingest_csv
from benchgap.report import mapping_summary, render_matrix, score_matrix

from conftest import CROSS_MIN_PAIRS, SEED

# Snapshot expectations for the test seed (regenerate if it changes).
N_MODELS = 206
N_VERSIONS = 20
N_MEASURED = 1187
N_TB_PAIRS = 12
TB4 = "aa-terminal-bench4/current@artificial-analysis"
TB21 = "aa-terminal-bench21/current@artificial-analysis"


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
    # labels and the featured flag come from the seed
    label, featured = conn.execute(
        "SELECT label, featured FROM benchmarks WHERE name = 'aa-terminal-bench4'"
    ).fetchone()
    assert label == "AA Terminal-Bench 4.0" and featured == 1
    # idempotent
    ingest_csv(conn, SEED)
    assert (
        conn.execute("SELECT COUNT(*) FROM scores WHERE source = 'measured'").fetchone()[0]
        == N_MEASURED
    )


def test_capabilities_stored(gapfilled_db):
    caps = {
        r["capability"]
        for r in gapfilled_db.execute("SELECT DISTINCT capability FROM benchmarks")
    }
    assert {"vision", "agentic-terminal", "agentic-tool", "knowledge"} <= caps


def test_fit_within_capability_and_quality_gate(build):
    conn, summary = build["conn"], build["fit"]
    stored = [s for s in summary if s["best_method"] is not None]
    rejected = [s for s in summary if s["best_method"] is None]
    assert stored, "expected several mappings to pass the gate"
    assert rejected, "expected some weak pairs to be rejected by the quality gate"

    # the Terminal-Bench mapping is stored in both directions
    by_dir = {(s["from"], s["to"]): s for s in stored}
    fwd = by_dir[(TB4, TB21)]
    rev = by_dir[(TB21, TB4)]
    assert fwd["n_pairs"] == rev["n_pairs"] == N_TB_PAIRS
    assert fwd["best_method"] == rev["best_method"] == "hill"
    assert fwd["best_LOO_RMSE"] < rev["best_LOO_RMSE"]

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


def test_cross_fits_only_across_capabilities(build):
    """The cross-domain fits pair different capabilities, and gapfill never uses them."""
    conn, summary = build["conn"], build["crossfit"]
    rows = conn.execute(
        "SELECT c.passes, bf.capability AS cf, bt.capability AS ct FROM cross_mappings c"
        " JOIN benchmark_versions vf ON vf.id = c.from_version_id"
        " JOIN benchmark_versions vt ON vt.id = c.to_version_id"
        " JOIN benchmarks bf ON bf.id = vf.benchmark_id"
        " JOIN benchmarks bt ON bt.id = vt.benchmark_id ORDER BY c.id"
    ).fetchall()
    assert len(rows) == len(summary) == 14
    assert all(r["cf"] != r["ct"] for r in rows)
    assert [bool(r["passes"]) for r in rows] == [s["rejected"] is None for s in summary]
    assert any(r["passes"] for r in rows) and not all(r["passes"] for r in rows)
    # every estimate comes from a mapping (or a multivariate mapping), never a cross fit
    assert conn.execute(
        "SELECT COUNT(*) FROM scores WHERE source = 'gapfilled'"
        " AND mapping_id IS NULL AND multi_mapping_id IS NULL"
    ).fetchone()[0] == 0


def test_fit_cache(writable_db, tmp_path):
    """A run from the cache stores exactly what fitting again does; checkpoints keep earlier results."""
    def stored():
        return [tuple(r) for r in writable_db.execute(
            "SELECT from_version_id, to_version_id, method, params_json, metrics_json, passes"
            " FROM cross_mappings ORDER BY id")]

    cold = FitCache(tmp_path, "crossfit")
    summary = fit_cross_mappings(writable_db, min_pairs=CROSS_MIN_PAIRS + 10, cache=cold)
    cold.save()
    fitted = stored()
    warm = FitCache(tmp_path, "crossfit")
    assert fit_cross_mappings(writable_db, min_pairs=CROSS_MIN_PAIRS + 10, cache=warm) == summary
    assert stored() == fitted and warm.stats() == f" ({len(warm.used)} of {len(warm.used)} fits reused)"

    cache = FitCache(tmp_path, "unit")
    cache.earlier = {"old": 1}
    cache.get("new", lambda: 2)
    cache.checkpoint()
    assert json.loads((tmp_path / "unit.json").read_text()) == {"old": 1, "new": 2}
    cache.save()
    assert json.loads((tmp_path / "unit.json").read_text()) == {"new": 2}


def test_gapfill_keeps_capability_gaps(gapfilled_db, writable_db):
    n_filled = gapfilled_db.execute(
        "SELECT COUNT(*) FROM scores WHERE source = 'gapfilled'"
    ).fetchone()[0]
    assert n_filled > 100
    assert gapfilled_db.execute(
        "SELECT COUNT(*) FROM scores WHERE source = 'gapfilled' AND NOT value BETWEEN 0 AND 1"
    ).fetchone()[0] == 0, "predictions are clamped into [0, 1]"

    # every univariate gapfilled row is traceable and same-capability
    rows = gapfilled_db.execute(
        "SELECT s.*, m.method, bt.capability AS to_cap, bf.capability AS from_cap"
        " FROM scores s"
        " JOIN mappings m ON m.id = s.mapping_id"
        " JOIN benchmark_versions vf ON vf.id = m.from_version_id"
        " JOIN benchmark_versions vt ON vt.id = s.version_id"
        " JOIN benchmarks bf ON bf.id = vf.benchmark_id"
        " JOIN benchmarks bt ON bt.id = vt.benchmark_id"
        " WHERE s.source = 'gapfilled'"
    ).fetchall()
    n_multi = gapfilled_db.execute(
        "SELECT COUNT(*) FROM scores WHERE source = 'gapfilled' AND multi_mapping_id IS NOT NULL"
    ).fetchone()[0]
    assert len(rows) + n_multi == n_filled
    for r in rows:
        meta = json.loads(r["prediction_json"])
        assert meta["method"] == r["method"]
        assert isinstance(meta["extrapolated"], bool)
        assert r["to_cap"] == r["from_cap"], "gapfilled across capabilities"

    # gaps are kept: no model gets an estimate in a capability it has no
    # measured score in, and many cells stay empty
    foreign = gapfilled_db.execute(
        "SELECT COUNT(*) FROM scores s"
        " JOIN benchmark_versions v ON v.id = s.version_id"
        " JOIN benchmarks b ON b.id = v.benchmark_id"
        " WHERE s.source = 'gapfilled' AND NOT EXISTS ("
        "   SELECT 1 FROM scores sm"
        "   JOIN benchmark_versions vm ON vm.id = sm.version_id"
        "   JOIN benchmarks bm ON bm.id = vm.benchmark_id"
        "   WHERE sm.model_id = s.model_id AND sm.source = 'measured'"
        "   AND bm.capability = b.capability)"
    ).fetchone()[0]
    assert foreign == 0, "estimated in a capability the model was never measured in"
    n_measured = gapfilled_db.execute(
        "SELECT COUNT(*) FROM scores WHERE source = 'measured'"
    ).fetchone()[0]
    assert n_measured + n_filled < N_MODELS * N_VERSIONS / 2, "capability gaps must stay gaps"

    # gapfill is idempotent
    assert len(gapfill(writable_db)) == n_filled


def test_report(gapfilled_db):
    summary = mapping_summary(gapfilled_db)
    assert len({s["method"] for s in summary}) >= 3, "several curve families win somewhere"

    columns, rows = score_matrix(gapfilled_db)
    assert len(columns) == N_VERSIONS
    assert len(rows) == N_MODELS
    kinds = {c["kind"] for r in rows for c in r["cells"]}
    assert kinds == {"m", "g", "missing"}

    text = render_matrix(gapfilled_db)
    assert "gapfilled" in text


def test_measured_beats_gapfilled(writable_db):
    conn = writable_db
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
