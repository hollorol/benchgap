"""Tests for the dense-core display filter."""
from __future__ import annotations

from benchgap.report import (
    DEFAULT_MIN_BENCHMARKS,
    DEFAULT_MIN_MODELS,
    best_mappings,
    display_filter,
    predictability_matrix,
    score_matrix,
)


def test_filter_is_a_fixpoint(gapfilled_db):
    """Every shown version/model meets both thresholds within the shown set."""
    vids, mids = display_filter(gapfilled_db, DEFAULT_MIN_MODELS, DEFAULT_MIN_BENCHMARKS)
    shown = {
        (r["model_id"], r["version_id"])
        for r in gapfilled_db.execute(
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


def test_sparse_benchmarks_and_thin_models_hidden(gapfilled_db):
    vids, mids = display_filter(gapfilled_db)
    labels = {
        r["id"]: f"{r['benchmark']}/{r['version']}"
        for r in gapfilled_db.execute(
            "SELECT v.id, v.version, b.name AS benchmark FROM benchmark_versions v"
            " JOIN benchmarks b ON b.id = v.benchmark_id"
        )
    }
    shown_labels = {labels[v] for v in vids}
    # benchmarks with only a handful of measured models drop out
    for hidden in (
        "aa-mmlu-pro/current",
        "aa-global-mmlu-lite/current",
        "aa-math500/current",
        "aa-aime2025/current",
        "aa-live-code-bench/current",
        "terminal-bench-hard/current",
        "terminal-bench-science/current",
    ):
        assert hidden not in shown_labels
    # models measured on very few benchmarks drop out
    shown_models = {
        r["slug"]
        for r in gapfilled_db.execute("SELECT id, slug FROM models")
        if r["id"] in mids
    }
    assert "claude-3-opus" not in shown_models
    # the dense frontier stays
    assert "aa-terminal-bench4/current" in shown_labels
    assert "aa-hle/current" in shown_labels
    assert "minimax-m3" in shown_models


def test_zero_thresholds_show_everything(gapfilled_db):
    vids, mids = display_filter(gapfilled_db, 0, 0)
    assert len(vids) == gapfilled_db.execute("SELECT COUNT(*) FROM benchmark_versions").fetchone()[0]
    assert len(mids) == gapfilled_db.execute("SELECT COUNT(*) FROM models").fetchone()[0]


def test_matrices_respect_the_filter(gapfilled_db):
    vids, mids = display_filter(gapfilled_db)
    cols, rows = score_matrix(gapfilled_db, vids, mids)
    assert len(cols) == len(vids)
    assert len(rows) == len(mids)
    # the unfiltered call still shows everything
    all_cols, all_rows = score_matrix(gapfilled_db)
    assert len(all_cols) == 20 and len(all_rows) == 206

    vers, cells = predictability_matrix(gapfilled_db, vids)
    assert len(vers) == len(vids)
    shown_maps = [
        m
        for m in best_mappings(gapfilled_db)
        if m["from_version_id"] in vids and m["to_version_id"] in vids
    ]
    assert sum(1 for row in cells for c in row if c is not None) == len(shown_maps)


def test_filtered_view_is_denser(gapfilled_db):
    vids, mids = display_filter(gapfilled_db)
    cols, rows = score_matrix(gapfilled_db, vids, mids)
    dashes = sum(1 for r in rows for c in r["cells"] if c["kind"] == "missing")
    all_cols, all_rows = score_matrix(gapfilled_db)
    all_dashes = sum(1 for r in all_rows for c in r["cells"] if c["kind"] == "missing")
    assert dashes / (len(rows) * len(cols)) < all_dashes / (len(all_rows) * len(all_cols))
