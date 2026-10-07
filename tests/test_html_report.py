"""Tests for the self-contained HTML report."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchgap.db import connect, init_db
from benchgap.fit import fit_mappings
from benchgap.gapfill import gapfill
from benchgap.html_report import generate_html_report
from benchgap.ingest import ingest_csv

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
    n_gapfilled = built_db.execute(
        "SELECT COUNT(*) FROM scores WHERE source = 'gapfilled'"
    ).fetchone()[0]
    n_models = built_db.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    n_used = built_db.execute(
        "SELECT COUNT(DISTINCT mapping_id) FROM scores WHERE source = 'gapfilled'"
    ).fetchone()[0]

    out = tmp_path / "sub" / "report.html"
    generate_html_report(built_db, out)

    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "benchgap report" in html
    assert "mm_offset" in html
    # figure cards for the mappings used by gapfill, capped at 12
    n_cards = min(12, n_used)
    assert html.count("data:image/png;base64,") == n_cards
    # every gapfilled cell is rendered and marked
    assert html.count('class="gapfilled"') == n_gapfilled
    # model rows plus the two header rows
    assert html.count("<tr><th class='rowhead") == n_models + 2
    # capability grouping present in the matrix header
    assert "rowhead cap" in html
    assert "capability" in html
    # gaps are visible as dashes
    assert html.count('class="missing">') > 0
