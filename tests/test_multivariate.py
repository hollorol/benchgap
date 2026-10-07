"""Tests for multivariate gapfill (several benchmarks -> one)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from benchgap.db import connect, init_db
from benchgap.fit import MAX_LOO_RMSE, MIN_PAIRS, MIN_R2, fit_mappings
from benchgap.gapfill import gapfill
from benchgap.ingest import ingest_csv
from benchgap.multivariate import fit_multimappings, fit_mv, mv_metrics, predict_mv
from benchgap.report import mapping_summary, multi_mapping_summary

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed" / "scores.csv"


# --- model-level ---------------------------------------------------------------


def test_mm_mv_recovers_synthetic_mixture():
    rng = np.random.default_rng(1)
    n = 60
    x1 = rng.uniform(0.05, 0.95, n)
    x2 = rng.uniform(0.05, 0.95, n)
    X = np.column_stack([x1, x2])
    s = 0.7 * x1 + 0.3 * x2
    y = 0.2 + 0.6 * s / (0.1 + s) + rng.normal(0, 0.01, n)
    params = fit_mv("mm_mv", X, y)
    pred = predict_mv("mm_mv", params, X)
    assert np.corrcoef(pred, y)[0, 1] > 0.95
    # weights recover the mixing ratio
    assert params["weights"][0] == pytest.approx(0.7, abs=0.15)
    assert params["weights"][1] == pytest.approx(0.3, abs=0.15)
    m = mv_metrics("mm_mv", params, X, y)
    assert np.isfinite(m["LOO_RMSE"])


def test_linear_mv_is_ridge_with_loo_chosen_alpha():
    rng = np.random.default_rng(2)
    n = 50
    X = np.column_stack([rng.uniform(0, 1, n), rng.uniform(0, 1, n)])
    y = 0.1 + 0.5 * X[:, 0] - 0.2 * X[:, 1] + rng.normal(0, 0.01, n)
    params = fit_mv("linear_mv", X, y)
    assert params["alpha"] > 0
    pred = predict_mv("linear_mv", params, X)
    assert np.all((pred >= 0) & (pred <= 1)), "ridge predictions clipped to [0, 1]"
    assert params["coef"][0] == pytest.approx(0.5, abs=0.1)
    assert params["coef"][1] == pytest.approx(-0.2, abs=0.1)


# --- pipeline-level -------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("mv") / "mv.db")
    init_db(conn)
    ingest_csv(conn, SEED)
    fit_mappings(conn)
    fit_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    gapfill(conn)
    yield conn
    conn.close()


def test_multi_mappings_stored(built):
    multi = multi_mapping_summary(built)
    # every stored multi-mapping uses at least two source benchmarks and
    # beat the best univariate alternative (TB 4.0's multi is rejected
    # because hill from tb-science is better; HLE's two-feature fit wins)
    assert all(len(s["features"]) >= 2 for s in multi)
    by_target = {s["target"]: s for s in multi}
    assert "hle/1.0@artificial-analysis" in by_target
    hle = by_target["hle/1.0@artificial-analysis"]
    assert hle["method"] == "linear_mv"
    assert set(hle["features"]) == {
        "critpt/1.0@artificial-analysis",
        "gpqa/diamond@artificial-analysis",
    }


def test_multi_beats_univariate_alternatives(built):
    multi = multi_mapping_summary(built)
    uni = mapping_summary(built)
    for s in multi:
        uni_loos = [u["LOO_RMSE_pp"] for u in uni if u["to"] == s["target"]]
        assert uni_loos, f"no univariate mapping for {s['target']}"
        assert s["LOO_RMSE_pp"] <= min(uni_loos) + 1e-6


def test_gapfill_uses_multi_when_features_available(built):
    multi_rows = built.execute(
        "SELECT * FROM scores WHERE source = 'gapfilled' AND multi_mapping_id IS NOT NULL"
    ).fetchall()
    assert len(multi_rows) >= 2
    for r in multi_rows:
        meta = json.loads(r["prediction_json"])
        assert meta["kind"] == "multi"
        assert len(meta["input_scores"]) >= 2
        assert r["mapping_id"] is None
    # models missing a feature fall back to the univariate path even for a
    # target that has a multi-mapping (hle has one)
    fallback = built.execute(
        "SELECT COUNT(*) FROM scores s"
        " JOIN models m ON m.id = s.model_id"
        " WHERE s.source = 'gapfilled' AND s.multi_mapping_id IS NULL"
        " AND s.version_id = (SELECT id FROM benchmark_versions WHERE version = '1.0'"
        "   AND benchmark_id = (SELECT id FROM benchmarks WHERE name = 'hle'))"
    ).fetchone()[0]
    assert fallback > 0, "expected univariate fallback predictions for hle"


def test_multifit_rerun_after_gapfill(built):
    # re-running the multivariate fit must not break foreign keys: it drops
    # dependent gapfilled rows first, and gapfill re-fills afterwards
    fit_multimappings(built, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    filled = gapfill(built)
    assert len(filled) > 100
    n_multi = sum(1 for f in filled if f["kind"] == "multi")
    assert n_multi >= 2
