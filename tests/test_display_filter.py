"""Tests for the dense-core display filter."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchgap.db import connect, init_db
from benchgap.fit import fit_mappings
from benchgap.gapfill import gapfill
from benchgap.ingest import ingest_csv
from benchgap.report import (
    DEFAULT_MIN_BENCHMARKS,
    DEFAULT_MIN_MODELS,
    best_mappings,
    display_filter,
    predictability_matrix,
    score_matrix,
)

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed" / "scores.csv"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("filter") / "filter.db")
    init_db(conn)
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
    yield conn
    conn.close()


def test_filter_is_a_fixpoint(built):
    """Every shown version/model meets both thresholds within the shown set."""
    vids, mids = display_filter(built, DEFAULT_MIN_MODELS, DEFAULT_MIN_BENCHMARKS)
    shown = {
        (r["model_id"], r["version_id"])
        for r in built.execute(
            "SELECT model_id, version_id FROM scores WHERE source = 'measured'"
        )
        if r["model_id"] in mids and r["version_id"] in vids
    }
    version_count: dict[int, int] = {}
    model_count: dict[int, int] = {}
    for m, v in shown:
        version_count[v] = version_count.get(v, 0) + 1
        model_count[m] = model_count.get(m, 0) + 1
    assert all(c >= DEFAULT_MIN_MODELS for c in version_count.values())
    assert all(c >= DEFAULT_MIN_BENCHMARKS for c in model_count.values())


def test_sparse_benchmarks_and_thin_models_hidden(built):
    vids, mids = display_filter(built)
    labels = {
        r["id"]: f"{r['benchmark']}/{r['version']}"
        for r in built.execute(
            "SELECT v.id, v.version, b.name AS benchmark FROM benchmark_versions v"
            " JOIN benchmarks b ON b.id = v.benchmark_id"
        )
    }
    shown_labels = {labels[v] for v in vids}
    # benchmarks with only a handful of measured models drop out
    for hidden in (
        "mmlu-pro/1.0",
        "global-mmlu-lite/1.0",
        "math-500/1.0",
        "aime-2025/2025",
        "livecodebench/1.0",
        "terminal-bench-hard/1.0",
        "ifbench/1.0",
    ):
        assert hidden not in shown_labels
    # models measured on very few benchmarks drop out
    shown_models = {
        r["slug"]
        for r in built.execute("SELECT id, slug FROM models")
        if r["id"] in mids
    }
    assert "claude-4-sonnet" not in shown_models
    # the dense frontier stays
    assert "terminal-bench/4.0" in shown_labels
    assert "hle/1.0" in shown_labels
    assert "claude-fable-5-1-max-with-fallback" in shown_models


def test_zero_thresholds_show_everything(built):
    vids, mids = display_filter(built, 0, 0)
    assert len(vids) == built.execute("SELECT COUNT(*) FROM benchmark_versions").fetchone()[0]
    assert len(mids) == built.execute("SELECT COUNT(*) FROM models").fetchone()[0]


def test_matrices_respect_the_filter(built):
    vids, mids = display_filter(built)
    cols, rows = score_matrix(built, vids, mids)
    assert len(cols) == len(vids)
    assert len(rows) == len(mids)
    # the unfiltered call still shows everything
    all_cols, all_rows = score_matrix(built)
    assert len(all_cols) == 24 and len(all_rows) == 85

    vers, cells = predictability_matrix(built, vids)
    assert len(vers) == len(vids)
    shown_maps = [
        m
        for m in best_mappings(built)
        if m["from_version_id"] in vids and m["to_version_id"] in vids
    ]
    assert sum(1 for row in cells for c in row if c is not None) == len(shown_maps)


def test_filtered_view_is_denser(built):
    vids, mids = display_filter(built)
    cols, rows = score_matrix(built, vids, mids)
    dashes = sum(1 for r in rows for c in r["cells"] if c["kind"] == "missing")
    all_cols, all_rows = score_matrix(built)
    all_dashes = sum(1 for r in all_rows for c in r["cells"] if c["kind"] == "missing")
    assert dashes / (len(rows) * len(cols)) < all_dashes / (len(all_rows) * len(all_cols))
