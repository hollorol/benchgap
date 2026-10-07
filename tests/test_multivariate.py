"""Tests for multivariate gapfill (several benchmarks -> one)."""
from __future__ import annotations

import json
import numpy as np
import pytest

from benchgap.fit import MAX_LOO_RMSE, MIN_PAIRS, MIN_R2
from benchgap.gapfill import gapfill
from benchgap.multivariate import fit_multimappings, fit_mv, mv_metrics, predict_mv
from benchgap.report import mapping_summary, multi_mapping_summary

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

GDP = "aa-gdp-pdf/current@artificial-analysis"


def test_multi_mappings_stored(gapfilled_db):
    multi = multi_mapping_summary(gapfilled_db)
    # every stored multi-mapping uses at least two source benchmarks and
    # beat the best univariate alternative; in the test seed, GDP.pdf is
    # predicted best from EnterpriseOps-Gym and AutomationBench together
    assert all(len(s["features"]) >= 2 for s in multi)
    by_target = {s["target"]: s for s in multi}
    gdp = by_target[GDP]
    assert gdp["method"] == "mm_mv"
    assert set(gdp["features"]) == {
        "aa-enterprise-ops-gym/current@artificial-analysis",
        "aa-automation-bench/current@artificial-analysis",
    }


def test_multi_beats_univariate_alternatives(gapfilled_db):
    multi = multi_mapping_summary(gapfilled_db)
    uni = mapping_summary(gapfilled_db)
    for s in multi:
        uni_loos = [u["LOO_RMSE_pp"] for u in uni if u["to"] == s["target"]]
        assert uni_loos, f"no univariate mapping for {s['target']}"
        assert s["LOO_RMSE_pp"] <= min(uni_loos) + 1e-6


def test_gapfill_uses_multi_when_features_available(gapfilled_db):
    multi_rows = gapfilled_db.execute(
        "SELECT * FROM scores WHERE source = 'gapfilled' AND multi_mapping_id IS NOT NULL"
    ).fetchall()
    assert multi_rows
    for r in multi_rows:
        meta = json.loads(r["prediction_json"])
        assert meta["kind"] == "multi"
        assert len(meta["input_scores"]) >= 2
        assert r["mapping_id"] is None
    # models missing a feature fall back to the univariate path even for a
    # target that has a multi-mapping
    fallback = gapfilled_db.execute(
        "SELECT COUNT(*) FROM scores s"
        " WHERE s.source = 'gapfilled' AND s.multi_mapping_id IS NULL"
        " AND s.version_id = (SELECT v.id FROM benchmark_versions v"
        "   JOIN benchmarks b ON b.id = v.benchmark_id WHERE b.name = 'aa-gdp-pdf')"
    ).fetchone()[0]
    assert fallback > 0, "expected univariate fallback predictions for GDP.pdf"


def test_multifit_rerun_after_gapfill(writable_db):
    # re-running the multivariate fit must not break foreign keys: it drops
    # dependent gapfilled rows first, and gapfill re-fills afterwards
    fit_multimappings(writable_db, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    filled = gapfill(writable_db)
    assert len(filled) > 100
    n_multi = sum(1 for f in filled if f["kind"] == "multi")
    assert n_multi >= 1
