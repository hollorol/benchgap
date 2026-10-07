"""Tests for the self-contained HTML report."""
from __future__ import annotations

from pathlib import Path

from benchgap.db import connect, init_db
from benchgap.fit import fit_mappings
from benchgap.gapfill import gapfill
from benchgap.html_report import generate_html_report
from benchgap.ingest import ingest_csv

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed" / "scores.csv"


def test_html_report(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    gapfill(conn)

    out = tmp_path / "sub" / "report.html"
    generate_html_report(conn, out)

    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "benchgap report" in html
    assert "mm_offset" in html
    # both mapping directions get a figure, embedded as data URIs
    assert html.count("data:image/png;base64,") == 2
    # all 14 gapfilled cells are rendered and marked
    assert html.count('class="gapfilled"') == 14
    # every model appears in the matrix (n model rows + 1 header row)
    n_models = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    assert html.count("<tr><th class='rowhead'>") == n_models + 1
    # measured values render with one decimal
    assert "59.6" in html
    conn.close()
