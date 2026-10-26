"""The masked-imputation experiment (nn.py): data prep always, training only with Keras."""
from __future__ import annotations

import importlib.util
import json

import numpy as np
import pytest

from benchgap.nn import (
    encode,
    fit_scaler,
    load_matrix,
    resolve_cap,
    sample_cap,
    sample_hide,
    split_models,
    targets,
)
from benchgap.report import display_filter


def test_load_matrix_is_the_measured_core(gapfilled_db):
    m = load_matrix(gapfilled_db, 10, 3)
    vids, mids = display_filter(gapfilled_db, 10, 3)
    assert {v["id"] for v in m.versions} <= vids and set(m.model_ids) <= mids
    measured = {
        (r["model_id"], r["version_id"]): r["value"]
        for r in gapfilled_db.execute("SELECT model_id, version_id, value FROM scores WHERE source = 'measured'")
    }
    for i, mid in enumerate(m.model_ids):
        for j, v in enumerate(m.versions):
            if m.observed[i, j]:
                assert m.X[i, j] == measured[(mid, v["id"])]
            else:
                assert (mid, v["id"]) not in measured  # gapfilled cells stay missing
    assert (m.observed.sum(axis=0) >= 10).all()


def test_split_models_is_a_disjoint_partition():
    tr, va = split_models(100, 0.2, np.random.default_rng(0))
    assert len(va) == 20 and len(tr) == 80
    assert set(tr) | set(va) == set(range(100)) and not set(tr) & set(va)


def _observed(rng, n=50, v=30):
    obs = rng.random((n, v)) < 0.3
    obs[:, :2] = True  # every row has at least two
    return obs


def test_sample_cap_trims_rich_rows_only():
    rng = np.random.default_rng(1)
    obs = _observed(rng)
    cap = 5
    a = sample_cap(obs, cap, rng)
    counts = obs.sum(axis=1)
    assert not (a & ~obs).any()
    assert (a.sum(axis=1) == np.minimum(counts, cap)).all()
    assert (a[counts <= cap] == obs[counts <= cap]).all()
    assert (sample_cap(obs, cap, rng) != a).any()  # resampled each call
    assert (sample_cap(obs, None, rng) == obs).all()


def test_resolve_cap():
    counts = np.array([3, 5, 9, 9, 20, 40])
    assert resolve_cap("median", counts) == 9
    assert resolve_cap("none", counts) is None
    assert resolve_cap(7, counts) == 7
    assert resolve_cap("p100", counts) == 40


def test_sample_hide_keeps_and_hides_one_at_least():
    obs = _observed(np.random.default_rng(2))
    hide = sample_hide(obs, 0.25, np.random.default_rng(3))
    assert not (hide & ~obs).any()
    assert (hide.sum(axis=1) >= 1).all()
    assert ((obs & ~hide).sum(axis=1) >= 1).all()
    assert (hide == sample_hide(obs, 0.25, np.random.default_rng(3))).all()


def test_fit_scaler_uses_training_rows_only():
    rng = np.random.default_rng(4)
    obs = _observed(rng)
    X = np.where(obs, rng.random(obs.shape), np.nan)
    tr = np.arange(40)
    X[40:] = np.where(obs[40:], 0.99, np.nan)  # validation rows must not move the stats
    mean, std = fit_scaler(X, obs, tr)
    z = encode(X[tr], obs[tr], mean, std)[:, : X.shape[1]]
    for j in range(X.shape[1]):
        col = z[obs[tr][:, j], j]
        if len(col) >= 2 and X[tr][obs[tr][:, j], j].std() > 0.02:
            assert abs(col.mean()) < 1e-5 and abs(col.std() - 1) < 1e-4


def test_encode_and_targets_pack_masks():
    X = np.array([[0.5, np.nan, 0.0]])
    visible = np.array([[True, False, False]])
    available = np.array([[True, False, True]])
    inp = encode(X, visible, np.array([0.5, 0.5, 0.5]), np.array([0.1, 0.1, 0.1]))
    assert inp.tolist() == [[0.0, 0.0, 0.0, 1.0, 0.0, 0.0]]
    assert targets(X, available).tolist() == [[0.5, 0.0, 0.0, 1.0, 0.0, 1.0]]


needs_keras = pytest.mark.skipif(importlib.util.find_spec("keras") is None, reason="needs keras (the nn extra)")


@needs_keras
def test_masked_loss_counts_measured_cells_only():
    from benchgap.nn import masked_mse

    X = np.array([[0.5, np.nan, 0.2]])
    y = targets(X, ~np.isnan(X))
    base = np.array([[0.4, 0.9, 0.2]], dtype="float32")
    loss = float(masked_mse(y, base))
    assert loss == pytest.approx(0.01 / 2)
    assert float(masked_mse(y, base + [[0, -0.8, 0]])) == pytest.approx(loss)  # unmeasured
    assert float(masked_mse(y, base + [[0, 0, 0.1]])) > loss  # a measured cell


@needs_keras
def test_untrained_model_predicts_the_means():
    from benchgap.nn import build_model

    mean = np.array([0.1, 0.5, 0.8, 0.3])
    model = build_model(4, mean, hidden=32)
    rng = np.random.default_rng(5)
    inp = np.concatenate([rng.normal(size=(16, 4)), rng.random((16, 4)) < 0.5], axis=1).astype("float32")
    pred = model.predict(inp, verbose=0)
    assert np.abs(pred - mean).max() < 0.01


@needs_keras
def test_wn_dense_starts_at_its_initializer_and_round_trips(tmp_path):
    import keras

    from benchgap.nn import build_model

    model = build_model(4, np.array([0.1, 0.5, 0.8, 0.3]), hidden=16)
    wn = [l for l in model.layers if type(l).__name__ == "WNDense"]
    assert len(wn) == 3
    v, g = wn[0].v.numpy(), wn[0].g.numpy()
    assert np.allclose(g, np.linalg.norm(v, axis=0))
    model.save(tmp_path / "m.keras")
    loaded = keras.saving.load_model(tmp_path / "m.keras", compile=False)
    inp = np.random.default_rng(6).normal(size=(3, 8)).astype("float32")
    assert np.allclose(model.predict(inp, verbose=0), loaded.predict(inp, verbose=0))


@needs_keras
def test_nnfit_writes_its_outputs(gapfilled_db, tmp_path):
    from benchgap.nn import nnfit

    m = nnfit(gapfilled_db, tmp_path, hidden=16, epochs=5, patience=5)
    assert {p.name for p in tmp_path.iterdir()} == {"model.keras", "metrics.json", "predictions.csv"}
    assert json.loads((tmp_path / "metrics.json").read_text())["epochs_run"] == 5
    assert m["val_rmse_init_pp"] == pytest.approx(m["baseline_rmse_pp"], abs=1.0)
    lines = (tmp_path / "predictions.csv").read_text().splitlines()
    assert lines[0] == "model_slug,benchmark,version,harness,predicted" and len(lines) > 1
