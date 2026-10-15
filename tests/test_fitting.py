"""Tests for the fitting module against the reference Terminal-Bench fit.

The reference values are the test seed's fitted MM+offset parameters on the
models measured on both Terminal-Bench 2.1 and 4.0 (Artificial Analysis's runs).
"""
from __future__ import annotations

import csv

import numpy as np
import pytest

from benchgap.fitting import (
    CANDIDATES,
    MAX_LOO_FOLDS,
    fit_all,
    fit_candidate,
    fit_metrics,
    is_increasing,
    predict,
    select_best,
)

from conftest import SEED

# Reference fit (fraction scale) on the test seed's 12 paired TB models.
REF_MM_OFFSET = {"y0": 0.4185705344255654, "vmax": 0.47644055312891176, "k": 0.01918231807066238}


@pytest.fixture(scope="module")
def pairs():
    by_model: dict[str, dict[str, float]] = {}
    with open(SEED, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["benchmark"] in ("aa-terminal-bench4", "aa-terminal-bench21"):
                by_model.setdefault(row["model_slug"], {})[row["benchmark"]] = float(row["score"])
    xs, ys = [], []
    for m in by_model.values():
        if len(m) == 2:
            xs.append(m["aa-terminal-bench4"])
            ys.append(m["aa-terminal-bench21"])
    assert len(xs) == 12
    return np.array(xs), np.array(ys)


def test_candidates_are_monotone_forms():
    """No quadratic; every candidate is a monotone mapping family."""
    assert set(CANDIDATES) == {
        "linear",
        "mm",
        "mm_offset",
        "mm_offset_inv",
        "hill",
        "logistic",
    }


def test_hill_generalizes_mm_offset(pairs):
    """Hill reduces to MM+offset at n=1, so its in-sample R2 can only match
    or beat the MM+offset fit on the same data."""
    xs, ys = pairs
    hill = fit_candidate("hill", xs, ys)
    mm = fit_candidate("mm_offset", xs, ys)
    assert hill.metrics["R2"] >= mm.metrics["R2"] - 1e-9
    assert hill.metrics["R2"] >= 0.97


def test_hill_and_logistic_are_monotone_and_bounded(pairs):
    xs, ys = pairs
    grid = np.linspace(0, 1, 300)
    for method in ("hill", "logistic"):
        r = fit_candidate(method, xs, ys)
        out = predict(method, r.params, grid)
        assert np.all(np.diff(out) >= -1e-9), f"{method} must be monotone"
        assert np.all(out <= 1.05)


def test_falling_fits_are_left_out():
    """A few models where a higher source score goes with a lower target score: the forms
    that can fall (linear, Hill, logistic) do, and fit_all keeps only rising fits."""
    rng = np.random.default_rng(3)
    xs = np.linspace(0.8, 0.93, 8)
    ys = 0.9 - 2.5 * (xs - 0.8) + rng.normal(0, 0.02, len(xs))
    falling = {m for m in ("linear", "hill", "logistic")
               if not is_increasing(m, fit_candidate(m, xs, ys).params)}
    assert falling
    results = fit_all(xs, ys)
    assert not falling & {r.method for r in results}
    for r in results:
        out = predict(r.method, r.params, np.linspace(0, 1, 300))
        assert np.all(np.diff(out) >= -1e-9), f"{r.method} must rise"


def test_is_increasing_by_parameters():
    assert is_increasing("linear", {"slope": 0.5, "intercept": 0.1})
    assert not is_increasing("linear", {"slope": -0.5, "intercept": 0.9})
    # the TB 2.1 -> 4.0 fit that estimated ~100% for a 9% model
    assert not is_increasing("hill", {"y0": 1.0, "a": 0.016, "k": 0.76494, "n": 6.0})
    assert is_increasing("hill", {"y0": 0.016, "a": 1.0, "k": 0.76494, "n": 6.0})
    assert not is_increasing("logistic", {"y0": 0.8, "a": 0.2, "k": 10.0, "xmid": 0.5})
    assert not is_increasing("mm_offset", {"y0": 0.3, "vmax": 0.0, "k": 0.1})


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
    assert r.metrics["R2"] == pytest.approx(0.9720, abs=1e-3)


def test_selection_picks_lowest_loo_rmse(pairs):
    xs, ys = pairs
    results = fit_all(xs, ys)
    best = select_best(results)
    assert best.method == "hill"
    assert best.metrics["LOO_RMSE"] == min(r.metrics["LOO_RMSE"] for r in results)


def test_reverse_prefers_inverse_mm_to_mm(pairs):
    """In the convex (reverse) direction the inverted MM form fits better than
    a saturating MM curve."""
    xs, ys = pairs
    loo = {r.method: r.metrics["LOO_RMSE"] for r in fit_all(ys, xs)}
    assert loo["mm_offset_inv"] < loo["mm_offset"]


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
