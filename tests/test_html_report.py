"""Tests for the self-contained HTML report."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchgap.db import connect, init_db
from benchgap.fit import fit_mappings
from benchgap.gapfill import gapfill
from benchgap.html_report import generate_html_report
from benchgap.ingest import ingest_csv
from benchgap.report import (
    DEFAULT_MIN_BENCHMARKS,
    DEFAULT_MIN_MODELS,
    best_mappings,
    display_filter,
    score_matrix,
)

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed" / "scores.csv"


@pytest.fixture(scope="module")
def built_db(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("html") / "report.db")
    init_db(conn)
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)
    yield conn
    conn.close()


def test_html_report(built_db, tmp_path):
    vids, mids = display_filter(built_db, DEFAULT_MIN_MODELS, DEFAULT_MIN_BENCHMARKS)
    cols, rows = score_matrix(built_db, vids, mids)
    n_gapfilled_shown = sum(1 for r in rows for c in r["cells"] if c["kind"] == "g")
    n_used = built_db.execute(
        "SELECT COUNT(DISTINCT mapping_id) FROM scores WHERE source = 'gapfilled'"
    ).fetchone()[0]
    shown_maps = [
        m
        for m in best_mappings(built_db)
        if m["from_version_id"] in vids and m["to_version_id"] in vids
    ]

    out = tmp_path / "sub" / "report.html"
    generate_html_report(built_db, out)

    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "benchgap report" in html
    assert "mm_offset" in html
    # figure cards for the mappings used by gapfill, capped at 12
    n_cards = min(12, n_used)
    assert html.count("data:image/png;base64,") == n_cards
    # the displayed score matrix is the dense-core view
    assert "Dense-core view" in html
    assert html.count('class="gapfilled"') == n_gapfilled_shown
    assert html.count("<tr><th class='rowhead") == len(rows) + 2
    # capability grouping present in the matrix header
    assert "rowhead cap" in html
    assert "capability" in html
    # multivariate mappings section reflects the database
    n_multi = built_db.execute("SELECT COUNT(*) FROM multi_mappings").fetchone()[0]
    if n_multi:
        assert "Multivariate mappings" in html
        assert html.count("<tr><td>") >= n_multi  # multi rows among the tables
    # predictability matrix over the filtered version set
    assert "Predictability matrix" in html
    assert html.count('style="background:rgb(') == len(shown_maps)
    assert html.count('class="pm diag') == len(vids)
    # the flagship mapping appears with its method tooltip
    assert 'title="mm_offset: n=15' in html


def test_html_report_unfiltered(built_db, tmp_path):
    """Thresholds of 0 disable the dense-core display filter."""
    out = tmp_path / "full.html"
    generate_html_report(built_db, out, min_models=0, min_benchmarks=0)
    html = out.read_text(encoding="utf-8")
    assert "Dense-core view" not in html
    n_all_models = built_db.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    assert html.count("<tr><th class='rowhead") == n_all_models + 2
