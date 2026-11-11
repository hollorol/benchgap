"""Neural gapfill (experimental): a masked-imputation network over the score matrix.

One small dense network sees a model's whole score vector and predicts every
benchmark at once. Each training row is masked twice: by what the model was
actually measured on, and by extra random hiding, so the network learns to
predict a cell it cannot see from the cells it can. The loss counts every
measured cell of the row (hidden and visible) and never an unmeasured one,
which has no ground truth; the reported validation error counts only the
hidden cells, since that is the gapfilling task.

- The matrix is the dense core of the measured fraction scores
  (report.display_filter). Unlike the curve fits, capability is ignored: the
  network learns from every benchmark at once.
- Models with many scores would dominate the loss and look unlike a typical
  input, so each epoch every row keeps at most ``cap`` of its measured cells
  (by default the median count of the training rows), resampled every epoch.
- Inputs are standardized per benchmark on the training models' measured
  scores; a hidden or unmeasured cell is 0 (the benchmark's mean) and a mask
  indicator marks it missing. Targets stay fractions, so the loss is in pp.
- The output layer starts at each benchmark's training mean (bias =
  logit(mean), near-zero kernel): the untrained network is the
  per-benchmark-mean baseline.

The split is by model (80/20 by default). Nothing here writes to the
database: ``nnfit`` saves the model, its metrics and its predictions to files.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .report import display_filter

STD_FLOOR = 0.02  # 2 pp: benchmarks with almost no spread do not blow up the inputs
MEAN_CLIP = (0.01, 0.99)  # keeps logit(mean) finite for the output bias
DEFAULT_MIN_MODELS = 10
DEFAULT_MIN_BENCHMARKS = 3


@dataclass
class Matrix:
    """Measured fraction scores of the dense core: rows are models, columns versions."""

    X: np.ndarray  # float, NaN where unmeasured
    observed: np.ndarray  # bool
    model_ids: list[int]
    slugs: list[str]
    versions: list[dict]  # id, benchmark, version, harness


def load_matrix(
    conn: sqlite3.Connection,
    min_models: int = DEFAULT_MIN_MODELS,
    min_benchmarks: int = DEFAULT_MIN_BENCHMARKS,
) -> Matrix:
    """The measured fraction scores of the core report.display_filter peels."""
    vids, mids = display_filter(conn, min_models, min_benchmarks)
    versions = [
        dict(r)
        for r in conn.execute(
            "SELECT v.id, b.name AS benchmark, v.version, v.harness"
            " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
            " WHERE v.unit = 'fraction' ORDER BY b.name, v.version, v.harness"
        )
        if r["id"] in vids
    ]
    models = [
        (r["id"], r["slug"])
        for r in conn.execute("SELECT id, slug FROM models ORDER BY slug")
        if r["id"] in mids
    ]
    col = {v["id"]: j for j, v in enumerate(versions)}
    row = {mid: i for i, (mid, _) in enumerate(models)}
    X = np.full((len(models), len(versions)), np.nan)
    for r in conn.execute(
        "SELECT model_id, version_id, value FROM scores WHERE source = 'measured'"
    ):
        if r["model_id"] in row and r["version_id"] in col:
            X[row[r["model_id"]], col[r["version_id"]]] = r["value"]
    observed = ~np.isnan(X)
    keep = observed.sum(axis=1) > 0
    return Matrix(
        X[keep],
        observed[keep],
        [m for (m, _), k in zip(models, keep) if k],
        [s for (_, s), k in zip(models, keep) if k],
        versions,
    )


def split_models(n: int, val_frac: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Row indices of the training and validation models, a random disjoint split."""
    perm = rng.permutation(n)
    n_val = int(round(n * val_frac))
    return np.sort(perm[n_val:]), np.sort(perm[:n_val])


def resolve_cap(spec: str | int | None, counts: np.ndarray) -> int | None:
    """The per-row cell cap: 'median', 'p<q>' (a percentile), an integer, or 'none'."""
    if spec is None or spec == "none":
        return None
    if isinstance(spec, str) and spec == "median":
        return max(2, int(np.median(counts)))
    if isinstance(spec, str) and spec.startswith("p"):
        return max(2, int(np.percentile(counts, float(spec[1:]))))
    return max(2, int(spec))


def sample_cap(observed: np.ndarray, cap: int | None, rng: np.random.Generator) -> np.ndarray:
    """Each row's available cells: a random ``cap`` of its observed ones, or all if fewer."""
    if cap is None:
        return observed.copy()
    available = np.zeros_like(observed)
    for i, row in enumerate(observed):
        idx = np.flatnonzero(row)
        if len(idx) > cap:
            idx = rng.choice(idx, cap, replace=False)
        available[i, idx] = True
    return available


def sample_hide(available: np.ndarray, rate: float, rng: np.random.Generator) -> np.ndarray:
    """Cells to hide from the input: about ``rate`` of each row's available cells,
    at least one hidden and one kept (rows with fewer than two hide nothing)."""
    hide = np.zeros_like(available)
    for i, row in enumerate(available):
        idx = np.flatnonzero(row)
        if len(idx) < 2:
            continue
        k = min(max(1, int(round(rate * len(idx)))), len(idx) - 1)
        hide[i, rng.choice(idx, k, replace=False)] = True
    return hide


def fit_scaler(X: np.ndarray, observed: np.ndarray, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-version mean and std of the given rows' measured scores (the training models).

    Versions with fewer than two such scores fall back to the pooled statistics;
    the std is floored at STD_FLOOR.
    """
    Xr, obs = X[rows], observed[rows]
    pooled = Xr[obs]
    mean = np.full(X.shape[1], pooled.mean())
    std = np.full(X.shape[1], max(pooled.std(), STD_FLOOR))
    for j in range(X.shape[1]):
        vals = Xr[obs[:, j], j]
        if len(vals) >= 2:
            mean[j] = vals.mean()
            std[j] = max(vals.std(), STD_FLOOR)
    return mean, std


def encode(X: np.ndarray, visible: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """Network inputs: standardized visible scores (0 elsewhere) and the visibility mask."""
    z = np.where(visible, (np.nan_to_num(X) - mean) / std, 0.0)
    return np.concatenate([z, visible], axis=1).astype("float32")


def targets(X: np.ndarray, available: np.ndarray) -> np.ndarray:
    """Packed y_true: the scores (0 where unavailable) and which of them the loss counts."""
    return np.concatenate([np.where(available, np.nan_to_num(X), 0.0), available], axis=1).astype("float32")


def masked_mse(y_true, y_pred):
    """Mean squared error over the cells y_true marks available (see targets)."""
    from keras import ops

    y_true = ops.cast(y_true, y_pred.dtype)
    n = ops.shape(y_pred)[-1]
    y, m = y_true[:, :n], y_true[:, n:]
    return ops.sum(m * ops.square(y_pred - y)) / ops.maximum(ops.sum(m), 1.0)


def build_model(
    n_versions: int,
    mean: np.ndarray,
    hidden: int | Sequence[int] = 128,
    depth: int = 3,
):
    """The dense masked-imputation network: 2V inputs (scores and mask) -> V fractions.

    ``hidden`` is one width for ``depth`` layers or a width per layer. Each
    hidden layer is a weight-normalized Dense (He init) -> LayerNorm -> GELU, no
    dropout; the output layer starts at each version's training mean: bias
    logit(mean), kernel near zero.
    """
    import keras
    from keras import layers

    WNDense = _wn_dense_class()

    lo, hi = MEAN_CLIP
    p = np.clip(mean, lo, hi)
    inputs = keras.Input((2 * n_versions,))
    h = inputs
    widths = [hidden] * depth if isinstance(hidden, int) else list(hidden)
    for width in widths:
        h = WNDense(width, kernel_initializer="he_normal")(h)
        h = layers.LayerNormalization()(h)
        h = layers.Activation("gelu")(h)
    outputs = layers.Dense(
        n_versions,
        activation="sigmoid",
        kernel_initializer=keras.initializers.RandomNormal(stddev=1e-3),
        bias_initializer=keras.initializers.Constant(np.log(p / (1 - p))),
    )(h)
    return keras.Model(inputs, outputs)


_WN_DENSE = None


def _wn_dense_class():
    """WNDense: Dense with weight normalization, kernel = g * v / ||v|| per unit
    (Salimans & Kingma 2016). Keras 3 has no such layer; g starts at ||v||, so
    the initial kernel is v as drawn by kernel_initializer. Registered for
    keras.saving.load_model once this has run (build_model calls it)."""
    global _WN_DENSE
    if _WN_DENSE is not None:
        return _WN_DENSE
    import keras
    from keras import ops

    @keras.saving.register_keras_serializable(package="benchgap")
    class WNDense(keras.layers.Layer):
        def __init__(self, units, kernel_initializer="glorot_uniform", **kwargs):
            super().__init__(**kwargs)
            self.units = units
            self.kernel_initializer = keras.initializers.get(kernel_initializer)

        def build(self, input_shape):
            self.v = self.add_weight(shape=(input_shape[-1], self.units), initializer=self.kernel_initializer, name="v")
            self.g = self.add_weight(shape=(self.units,), initializer="ones", name="g")
            self.g.assign(ops.sqrt(ops.sum(ops.square(self.v), axis=0)))
            self.bias = self.add_weight(shape=(self.units,), initializer="zeros", name="bias")

        def call(self, x):
            kernel = self.v * (self.g / ops.sqrt(ops.sum(ops.square(self.v), axis=0) + 1e-12))
            return ops.matmul(x, kernel) + self.bias

        def get_config(self):
            return {**super().get_config(), "units": self.units,
                    "kernel_initializer": keras.initializers.serialize(self.kernel_initializer)}

    _WN_DENSE = WNDense
    return WNDense


def _dataset_class():
    import keras

    class MaskedRows(keras.utils.PyDataset):
        """Training batches with fresh cap and hide masks (and row order) every epoch."""

        def __init__(self, X, observed, mean, std, cap, rate, batch_size, rng, **kwargs):
            super().__init__(**kwargs)
            self.X, self.observed, self.mean, self.std = X, observed, mean, std
            self.cap, self.rate, self.batch_size, self.rng = cap, rate, batch_size, rng
            self.on_epoch_end()

        def on_epoch_end(self):
            available = sample_cap(self.observed, self.cap, self.rng)
            hide = sample_hide(available, self.rate, self.rng)
            order = self.rng.permutation(len(self.X))
            self.inputs = encode(self.X, available & ~hide, self.mean, self.std)[order]
            self.y = targets(self.X, available)[order]

        def __len__(self):
            return int(np.ceil(len(self.X) / self.batch_size))

        def __getitem__(self, i):
            s = slice(i * self.batch_size, (i + 1) * self.batch_size)
            return self.inputs[s], self.y[s]

    return MaskedRows


def _rmse_pp(pred: np.ndarray, X: np.ndarray, cells: np.ndarray) -> float:
    return float(np.sqrt(np.mean((pred[cells] - X[cells]) ** 2)) * 100) if cells.any() else float("nan")


def pipeline_loo(conn: sqlite3.Connection) -> dict[int, float]:
    """The best stored LOO RMSE per target version, over univariate and multivariate mappings."""
    best: dict[int, float] = {}
    for table in ("mappings", "multi_mappings"):
        for r in conn.execute(
            f"SELECT to_version_id, json_extract(metrics_json, '$.LOO_RMSE') AS loo FROM {table}"
        ):
            if r["loo"] is not None:
                best[r["to_version_id"]] = min(best.get(r["to_version_id"], 1e9), r["loo"])
    return best


def nnfit(
    conn: sqlite3.Connection,
    out: str | Path = Path("data") / "nn",
    min_models: int = DEFAULT_MIN_MODELS,
    min_benchmarks: int = DEFAULT_MIN_BENCHMARKS,
    hidden: int | Sequence[int] = 128,
    depth: int = 3,
    cap: str | int | None = "median",
    hide_rate: float = 0.25,
    val_frac: float = 0.2,
    epochs: int = 2000,
    patience: int = 100,
    batch_size: int = 32,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    seed: int = 0,
    verbose: int = 0,
) -> dict:
    """Train the masked-imputation network, evaluate it on the validation models' hidden
    cells and write model.keras, metrics.json and predictions.csv to ``out``.

    Returns the metrics (as written to metrics.json).
    """
    import keras

    config = {k: v for k, v in locals().items() if k not in ("conn", "keras")}
    config["out"] = str(out)
    keras.utils.set_random_seed(seed)
    rng = np.random.default_rng(seed)

    m = load_matrix(conn, min_models, min_benchmarks)
    rows = np.flatnonzero(m.observed.sum(axis=1) >= 2)
    if len(rows) < 10:
        raise ValueError(f"only {len(rows)} models with two or more scores in the core")
    tr, va = split_models(len(rows), val_frac, rng)
    tr, va = rows[tr], rows[va]
    n_cap = resolve_cap(cap, m.observed[tr].sum(axis=1))
    mean, std = fit_scaler(m.X, m.observed, tr)

    # validation masks: drawn once, so the val loss is comparable across epochs
    val_available = sample_cap(m.observed[va], n_cap, rng)
    val_hide = sample_hide(val_available, hide_rate, rng)
    Xv = m.X[va]
    val_inputs = encode(Xv, val_available & ~val_hide, mean, std)
    val_data = (val_inputs, targets(Xv, val_available))

    model = build_model(m.X.shape[1], mean, hidden, depth)
    model.compile(
        optimizer=keras.optimizers.AdamW(learning_rate=lr, weight_decay=weight_decay),
        loss=masked_mse,
    )
    init_pred = model.predict(val_inputs, verbose=0)
    train_data = _dataset_class()(m.X[tr], m.observed[tr], mean, std, n_cap, hide_rate, batch_size, rng)
    history = model.fit(
        train_data,
        validation_data=val_data,
        epochs=epochs,
        verbose=verbose,
        callbacks=[keras.callbacks.EarlyStopping(patience=patience, restore_best_weights=True)],
    )
    val_loss = history.history["val_loss"]
    best_epoch = int(np.argmin(val_loss)) + 1

    pred = model.predict(val_inputs, verbose=0)
    # uncapped: the same hidden cells, every other measured score visible (as predictions.csv)
    pred_full = model.predict(encode(Xv, m.observed[va] & ~val_hide, mean, std), verbose=0)
    baseline = np.broadcast_to(mean, Xv.shape)
    loo = pipeline_loo(conn)
    per_version = []
    for j, v in enumerate(m.versions):
        cells = np.zeros_like(val_hide)
        cells[:, j] = val_hide[:, j]
        if cells.any():
            per_version.append({
                "version": f"{v['benchmark']}/{v['version']}@{v['harness']}",
                "n": int(cells.sum()),
                "rmse_pp": _rmse_pp(pred, Xv, cells),
                "baseline_pp": _rmse_pp(baseline, Xv, cells),
                "pipeline_loo_pp": loo[v["id"]] * 100 if v["id"] in loo else None,
            })
    per_version.sort(key=lambda r: r["rmse_pp"])

    metrics = {
        "config": config,
        "cap": n_cap,
        "shape": {"models": int(len(rows)), "versions": len(m.versions),
                  "measured": int(m.observed[rows].sum())},
        "train_models": int(len(tr)),
        "val_models": int(len(va)),
        "val_hidden_cells": int(val_hide.sum()),
        "best_epoch": best_epoch,
        "epochs_run": len(val_loss),
        "val_rmse_pp": _rmse_pp(pred, Xv, val_hide),
        "val_rmse_uncapped_pp": _rmse_pp(pred_full, Xv, val_hide),
        "val_rmse_init_pp": _rmse_pp(init_pred, Xv, val_hide),
        "baseline_rmse_pp": _rmse_pp(baseline, Xv, val_hide),
        "per_version": per_version,
        "scaler": {"mean": mean.tolist(), "std": std.tolist()},
        "history": {k: [float(x) for x in vals] for k, vals in history.history.items()},
    }

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    model.save(out / "model.keras")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1))
    # every unmeasured cell of the core, from all of its model's measured scores
    full = model.predict(encode(m.X, m.observed, mean, std), verbose=0)
    with open(out / "predictions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model_slug", "benchmark", "version", "harness", "predicted"])
        for i in rows:
            for j in np.flatnonzero(~m.observed[i]):
                v = m.versions[j]
                w.writerow([m.slugs[i], v["benchmark"], v["version"], v["harness"], f"{full[i, j]:.4f}"])
    return metrics
