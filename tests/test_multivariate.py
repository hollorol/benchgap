"""Tests for multivariate gapfill (several benchmarks -> one)."""
from __future__ import annotations

import csv
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

# --- model-level ---------------------------------------------------------------


def test_enet_mv_lasso_selects_the_real_features():
    """The lasso part zeroes the noise sources; the survivors are the mapping's features."""
    rng = np.random.default_rng(5)
    n = 20
    X = rng.uniform(0.05, 0.95, (n, 5))
    y = 0.1 + 0.5 * X[:, 0] + 0.3 * X[:, 1] + rng.normal(0, 0.1, n)
    params = fit_mv("enet_mv", X, y)
    keep = params.pop("keep")
    assert keep == [0, 1]
    assert params["coef"][0] == pytest.approx(0.5, abs=0.2)
    assert params["coef"][1] == pytest.approx(0.3, abs=0.3)
    assert params["coef"][0] > 0 and params["coef"][1] > 0
    pred = predict_mv("enet_mv", params, X[:, keep])
    assert np.all((pred >= 0) & (pred <= 1)), "predictions clipped to [0, 1]"
    assert np.corrcoef(pred, y)[0, 1] > 0.8
    m = mv_metrics("enet_mv", params, X[:, keep], y)
    assert np.isfinite(m["LOO_RMSE"])


def test_enet_mv_min_features_keeps_enough_sources():
    """min_features keeps weaker sources in the mapping; more than the pool has never fits."""
    rng = np.random.default_rng(4)
    n = 40
    X = rng.uniform(0.05, 0.95, (n, 3))
    y = 0.1 + 0.3 * X.sum(axis=1) + rng.normal(0, 0.03, n)
    assert fit_mv("enet_mv", X, y, 3)["keep"] == [0, 1, 2]
    with pytest.raises(RuntimeError):
        fit_mv("enet_mv", X, y, 4)


def test_enet_mv_constant_target_never_fits():
    rng = np.random.default_rng(6)
    X = rng.uniform(0.05, 0.95, (20, 3))
    with pytest.raises(RuntimeError):
        fit_mv("enet_mv", X, np.full(20, 0.5))


def test_mm_mv_wins_on_the_lasso_selected_features():
    """A saturating target: the elastic net selects the features, and on them the
    multivariate Michaelis-Menten beats the linear fit and recovers the mixing."""
    rng = np.random.default_rng(1)
    n = 60
    x1 = rng.uniform(0.05, 0.95, n)
    x2 = rng.uniform(0.05, 0.95, n)
    X = np.column_stack([x1, x2, rng.uniform(0, 1, n), rng.uniform(0, 1, n)])
    s = 0.7 * x1 + 0.3 * x2
    y = 0.2 + 0.6 * s / (0.1 + s) + rng.normal(0, 0.01, n)
    sel = fit_mv("enet_mv", X, y)
    keep = sel.pop("keep")
    assert keep[:2] == [0, 1], "the informative pair survives the lasso"
    p_mm = fit_mv("mm_mv", X[:, keep], y)
    m_enet = mv_metrics("enet_mv", sel, X[:, keep], y)
    m_mm = mv_metrics("mm_mv", p_mm, X[:, keep], y)
    assert m_mm["LOO_RMSE"] < m_enet["LOO_RMSE"], "the curve fits the saturating truth better"
    assert p_mm["weights"][0] == pytest.approx(0.7, abs=0.15)
    assert p_mm["weights"][1] == pytest.approx(0.3, abs=0.15)
    assert sum(p_mm["weights"][2:]) < 0.15, "the noise sources' weights stay small"


# --- pipeline-level -------------------------------------------------------------

GDP = "aa-gdp-pdf/current@artificial-analysis"


def test_multi_mappings_stored(gapfilled_db):
    multi = multi_mapping_summary(gapfilled_db)
    # every stored multi-mapping beat the best univariate alternative; one kept
    # by the lasso's single surviving source is still a multi-mapping (its
    # shrinkage can beat every univariate curve), a one-feature nonlinear fit
    # is not. In the test seed, GDP.pdf is predicted best from
    # EnterpriseOps-Gym and AutomationBench together, and on that pair the
    # saturating Michaelis-Menten wins
    assert all(len(s["features"]) >= 2 or s["method"] == "enet_mv" for s in multi)
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
        assert len(meta["input_scores"]) >= 1
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


def test_multifit_drops_stale_multi_mappings(writable_db):
    """A multi-mapping an earlier run stored must not survive a refit that rejects the
    target: gapfill could not predict from a method this pipeline no longer has."""
    conn = writable_db
    target = conn.execute(
        "SELECT v.id FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        " WHERE b.name = 'aa-hle'"
    ).fetchone()[0]
    features = json.dumps([r[0] for r in conn.execute(
        "SELECT v.id FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        " WHERE b.name != 'aa-hle' LIMIT 2"
    )])
    stale = conn.execute(
        "INSERT INTO multi_mappings (to_version_id, method, feature_version_ids_json,"
        " params_json, metrics_json, n_points) VALUES (?, 'linear_mv', ?, '{}', '{}', 5)",
        (target, features),
    ).lastrowid
    model = conn.execute(
        "SELECT m.id FROM models m WHERE NOT EXISTS"
        " (SELECT 1 FROM scores s WHERE s.model_id = m.id AND s.version_id = ?) LIMIT 1",
        (target,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO scores (model_id, version_id, value, source, multi_mapping_id)"
        " VALUES (?, ?, 0.5, 'gapfilled', ?)",
        (model, target, stale),
    )
    conn.commit()
    # aa-hle is rejected in this build ("does not beat best univariate"): the
    # stale row must go even though nothing replaces it
    fit_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    assert conn.execute(
        "SELECT COUNT(*) FROM multi_mappings WHERE method = 'linear_mv'"
    ).fetchone()[0] == 0, "a rejected refit must clear the target's old multi-mapping"
    assert conn.execute(
        "SELECT COUNT(*) FROM scores WHERE multi_mapping_id = ?", (stale,)
    ).fetchone()[0] == 0, "the gapfilled rows that referenced it must go with it"


# --- pipeline-level: a synthetic seed with a known multivariate signal ----------

CSV_FIELDS = [
    "model_slug", "model_name", "release", "benchmark", "version", "label", "featured",
    "capability", "unit", "harness", "score", "source_url", "retrieved_at",
]
SYNTH_FEATURES = {"synth-f1", "synth-f2"}


def _write_seed(path: Path) -> None:
    """A seed whose target is the average of two benchmarks plus noise: each source
    alone predicts it poorly (R2 ~ 0.5), both together almost perfectly. A third,
    pure-noise benchmark shares the capability, and the models exercise every gapfill
    path: measured on everything, on both features, or on one only."""
    rng = np.random.default_rng(7)
    rows = []

    def add(model: str, benchmark: str, score: float) -> None:
        rows.append(dict(
            model_slug=model, model_name=model.replace("-", " "), release="2026-01-01",
            benchmark=benchmark, version="current", label=benchmark, featured="0",
            capability="synth", unit="fraction", harness="artificial-analysis",
            score=round(float(score), 4), source_url="https://example.com", retrieved_at="2026-01-01",
        ))

    for i in range(30):  # measured on all four benchmarks
        f1, f2, noise = rng.uniform(0.15, 0.85, 3)
        add(f"train-{i}", "synth-f1", f1)
        add(f"train-{i}", "synth-f2", f2)
        add(f"train-{i}", "synth-noise", noise)
        add(f"train-{i}", "synth-target", 0.5 * f1 + 0.5 * f2 + rng.normal(0, 0.02))
    for i in range(6):  # missing the target, measured on every source
        f1, f2, noise = rng.uniform(0.15, 0.85, 3)
        add(f"fill-{i}", "synth-f1", f1)
        add(f"fill-{i}", "synth-f2", f2)
        add(f"fill-{i}", "synth-noise", noise)
    for i in range(4):  # missing the target and one source: the univariate path
        add(f"fallback-{i}", "synth-f1", rng.uniform(0.15, 0.85))

    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture(scope="session")
def synth_db(tmp_path_factory):
    """The synthetic seed, fitted, multifitted and gapfilled (read-only; write tests copy it)."""
    tmp = tmp_path_factory.mktemp("synth")
    seed = tmp / "seed.csv"
    _write_seed(seed)
    conn = connect(tmp / "seed.db")
    init_db(conn)
    ingest_csv(conn, seed)
    fit_mappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    fit_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
    gapfill(conn)
    yield conn
    conn.close()


def _target_row(conn):
    return conn.execute(
        "SELECT m.* FROM multi_mappings m JOIN benchmark_versions v ON v.id = m.to_version_id"
        " JOIN benchmarks b ON b.id = v.benchmark_id WHERE b.name = 'synth-target'"
    ).fetchone()


def test_synthetic_multi_mapping_from_lasso_features(synth_db):
    # the lasso part selects the two informative sources from the pool (the
    # noise benchmark included), and on a linear truth the linear fit wins
    row = _target_row(synth_db)
    assert row is not None, "the synthetic target must keep a multi-mapping"
    assert row["method"] == "enet_mv"
    features = json.loads(row["feature_version_ids_json"])
    names = {
        synth_db.execute(
            "SELECT b.name FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
            " WHERE v.id = ?", (f,)
        ).fetchone()[0]
        for f in features
    }
    assert names == SYNTH_FEATURES
    assert row["n_points"] == 30
    params = json.loads(row["params_json"])
    assert len(params["coef"]) == 2
    metrics = json.loads(row["metrics_json"])
    assert metrics["R2"] > 0.9
    assert metrics["LOO_RMSE"] < 0.05


def test_synthetic_gapfill_multi_and_fallback(synth_db):
    multi_rows = synth_db.execute(
        "SELECT s.*, b.name FROM scores s JOIN models m ON m.id = s.model_id"
        " JOIN benchmark_versions v ON v.id = s.version_id"
        " JOIN benchmarks b ON b.id = v.benchmark_id"
        " WHERE s.source = 'gapfilled' AND s.multi_mapping_id IS NOT NULL AND b.name = 'synth-target'"
    ).fetchall()
    assert len(multi_rows) == 6, "the fill models (both sources measured) take the multi path"
    for r in multi_rows:
        assert r["mapping_id"] is None
        meta = json.loads(r["prediction_json"])
        assert meta["kind"] == "multi"
        assert len(meta["input_scores"]) == 2
    # models missing one source fall back to the univariate path even for a
    # target that has a multi-mapping
    fallback = synth_db.execute(
        "SELECT COUNT(*) FROM scores s JOIN models m ON m.id = s.model_id"
        " WHERE s.source = 'gapfilled' AND s.multi_mapping_id IS NULL"
        " AND m.slug LIKE 'fallback-%'"
        " AND s.version_id = (SELECT v.id FROM benchmark_versions v"
        "   JOIN benchmarks b ON b.id = v.benchmark_id WHERE b.name = 'synth-target')"
    ).fetchone()[0]
    assert fallback == 4, "the fallback models (one source missing) take the univariate path"


def test_synthetic_multifit_rerun(tmp_path, synth_db):
    # re-running the multivariate fit must not break foreign keys: it drops
    # dependent gapfilled rows first, and gapfill re-fills afterwards
    conn = connect(tmp_path / "copy.db")
    synth_db.backup(conn)
    try:
        fit_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE)
        filled = gapfill(conn)
        assert sum(1 for f in filled if f["kind"] == "multi") == 6
    finally:
        conn.close()
