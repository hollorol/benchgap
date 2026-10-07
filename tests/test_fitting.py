"""Tests for the fitting module against the reference Terminal-Bench fit.

The reference values are the current seed's fitted MM+offset parameters on
the models measured on both Terminal-Bench 2.1 and 4.0 (Artificial Analysis
harness).
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from benchgap.fitting import (
    CANDIDATES,
    MAX_LOO_FOLDS,
    fit_all,
    fit_candidate,
    fit_metrics,
    predict,
    select_best,
)

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed" / "scores.csv"

# Reference fit (fraction scale) on the seed's 15 paired TB models.
REF_MM_OFFSET = {"y0": 0.39021899226781753, "vmax": 0.5037632668567648, "k": 0.014054323955429493}


@pytest.fixture(scope="module")
def pairs():
    by_model: dict[str, dict[str, float]] = {}
    with open(SEED, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["benchmark"] == "terminal-bench":
                by_model.setdefault(row["model_slug"], {})[row["version"]] = float(row["score"])
    xs, ys = [], []
    for m in by_model.values():
        if "2.1" in m and "4.0" in m:
            xs.append(m["4.0"])
            ys.append(m["2.1"])
    assert len(xs) == 15
    return np.array(xs), np.array(ys)


def test_candidates_are_monotone_forms():
    """No quadratic; every candidate is a monotone mapping family."""
    assert set(CANDIDATES) == {"linear", "mm", "mm_offset", "mm_offset_inv"}


def test_all_candidates_fit(pairs):
    xs, ys = pairs
    results = fit_all(xs, ys)
    assert len(results) == len(CANDIDATES)
    for r in results:
        # a mismatched family can legitimately earn negative R2; the point
        # here is that every candidate fits without error and produces
        # usable metrics
        assert np.isfinite(r.metrics["R2"]) and r.metrics["R2"] <= 1.0
        assert r.metrics["RMSE"] > 0


def test_mm_offset_reproduces_reference(pairs):
    xs, ys = pairs
    r = fit_candidate("mm_offset", xs, ys)
    assert r.params["y0"] == pytest.approx(REF_MM_OFFSET["y0"], abs=5e-3)
    assert r.params["vmax"] == pytest.approx(REF_MM_OFFSET["vmax"], abs=5e-3)
    assert r.params["k"] == pytest.approx(REF_MM_OFFSET["k"], abs=5e-3)
    assert r.metrics["R2"] == pytest.approx(0.9495, abs=1e-3)


def test_selection_forward_picks_mm_offset(pairs):
    xs, ys = pairs
    assert select_best(fit_all(xs, ys)).method == "mm_offset"


def test_selection_reverse_picks_inverse_mm(pairs):
    """The reverse direction uses the inverted MM form, not an independent
    polynomial fit."""
    xs, ys = pairs
    assert select_best(fit_all(ys, xs)).method == "mm_offset_inv"


def test_mm_forms_are_monotone(pairs):
    xs, ys = pairs
    grid = np.linspace(0, 1, 200)
    fwd = fit_candidate("mm_offset", xs, ys)
    out = predict("mm_offset", fwd.params, grid)
    assert np.all(np.diff(out) > 0), "mm_offset must be strictly monotone"
    rev = fit_candidate("mm_offset_inv", ys, xs)
    out = predict("mm_offset_inv", rev.params, grid)
    assert np.all(np.diff(out) >= 0), "mm_offset_inv must be monotone"
    assert np.all((out >= 0) & (out <= 1)), "inverse predictions stay in [0, 1]"


def test_loo_rmse_sane(pairs):
    xs, ys = pairs
    for r in fit_all(xs, ys):
        assert r.metrics["LOO_RMSE"] >= r.metrics["RMSE"] - 1e-9


def test_loo_folds_capped():
    """With many points, LOO uses a deterministic subsample and still works."""
    rng = np.random.default_rng(0)
    n = 120
    x = np.sort(rng.uniform(0.05, 0.9, n))
    y = 0.3 + 0.5 * x / (0.05 + x) + rng.normal(0, 0.01, n)
    r = fit_candidate("mm_offset", x, y)
    m = fit_metrics("mm_offset", r.params, x, y)
    assert m["n"] == n
    assert np.isfinite(m["LOO_RMSE"])
    assert MAX_LOO_FOLDS < n
