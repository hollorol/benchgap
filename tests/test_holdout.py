"""The masked-holdout evaluation results: the shipped summary, its storage and CLI.

The results are measured artifacts of one data snapshot (the paper's evaluation),
shipped with the package like the harness-tax seed registry; these tests check
that the shipped file is well-formed, that store/summary round-trip through the
database, and that the CLI replaces an earlier store instead of accumulating.
"""
from __future__ import annotations

from pathlib import Path

from benchgap import holdout
from benchgap.db import connect, init_db


def make_db(tmp_path: Path):
    db = connect(tmp_path / "test.db")
    init_db(db)
    return db


def test_shipped_results_are_well_formed():
    results = holdout.load_results()
    assert results["data_retrieved_at"] == "2026-10-08"
    runs = results["runs"]
    assert len(runs) == 5
    assert sum(1 for r in runs if r["scheme"] == "random") == 3
    assert sum(1 for r in runs if r["scheme"] == "sparse") == 2
    for run in runs:
        assert run["n_filled"] + run["n_refused"] == run["n_masked"]
        assert 0 < run["coverage"] <= 1
        assert 0 < run["pipeline"]["mae_pp"] < 100
        assert set(run["by_level"]) == {"high", "medium", "low"}
        for level in run["by_level"].values():
            assert level["n"] > 0 and level["mae_pp"] >= 0
        assert run["baselines"]["svd2_on_filled"]["mae_pp"] > run["pipeline"]["mae_pp"]
    headline = results["headline"]
    assert headline["n_runs"] == len(runs)
    assert headline["reweighted"]["mae_pp"] > runs[0]["pipeline"]["mae_pp"]
    assert headline["paired_diff_pp"][0] < 0  # the pipeline beats the completion per cell
    assert headline["sparse_coverage"]["k1"] < headline["sparse_coverage"]["k2"]


def test_store_and_summary_round_trip(tmp_path):
    db = make_db(tmp_path)
    assert holdout.summary(db) == {"headline": None, "runs": []}
    stored = holdout.store(db)
    assert len(stored) == 5
    out = holdout.summary(db)
    assert out["headline"] == holdout.load_results()["headline"]
    assert [r["run_id"] for r in out["runs"]] == [r["run_id"] for r in stored]
    assert out["runs"][0]["pipeline"]["mae_pp"] == stored[0]["pipeline"]["mae_pp"]
    # storing again replaces instead of accumulating
    holdout.store(db)
    assert len(holdout.summary(db)["runs"]) == 5


def test_cli_stores_and_reports(tmp_path, capsys, monkeypatch):
    from benchgap.cli import main

    path = tmp_path / "cli.db"
    db = connect(path)
    init_db(db)
    db.close()
    monkeypatch.setattr("sys.argv", ["benchgap", "--db", str(path), "holdout"])
    main()
    out = capsys.readouterr().out
    assert "stored 5 holdout runs" in out
    assert "sparse-k1" in out and "headline:" in out
    assert "6.3 pp MAE" in out
