"""Tests for the fitting module against the reference Terminal-Bench fit.

The reference values come from the original analysis session (exported in
tb_model_comparison.json): the MM+offset fit on 20 paired Artificial
Analysis scores (TB 2.1 vs TB 4.0, fractions).
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from benchgap.fitting import CANDIDATES, fit_all, fit_candidate, predict, select_best

REPO = Path(__file__).resolve().parent.parent
PAIRED = REPO / "data" / "seed" / "scores.csv"

# Reference fit (fraction scale), from the prior analysis session.
REF_MM_OFFSET = {"y0": 0.3956974039962012, "vmax": 0.505937182623131, "k": 0.015057051886479567}


@pytest.fixture(scope="module")
def pairs():
    by_model: dict[str, dict[str, float]] = {}
    with open(PAIRED, newline="") as fh:
        for row in csv.DictReader(fh):
            by_model.setdefault(row["model_slug"], {})[row["version"]] = float(row["score"])
    xs, ys = [], []
    for m in by_model.values():
        if "2.1" in m and "4.0" in m:
            xs.append(m["4.0"])
            ys.append(m["2.1"])
    assert len(xs) == 20
    return np.array(xs), np.array(ys)


def test_all_candidates_fit(pairs):
    xs, ys = pairs
    results = fit_all(xs, ys)
    assert {r.method for r in results} == set(CANDIDATES)
    for r in results:
        assert 0.0 <= r.metrics["R2"] <= 1.0
        assert r.metrics["RMSE"] > 0


def test_mm_offset_reproduces_reference(pairs):
    xs, ys = pairs
    r = fit_candidate("mm_offset", xs, ys)
    assert r.params["y0"] == pytest.approx(REF_MM_OFFSET["y0"], abs=5e-3)
    assert r.params["vmax"] == pytest.approx(REF_MM_OFFSET["vmax"], abs=5e-3)
    assert r.params["k"] == pytest.approx(REF_MM_OFFSET["k"], abs=5e-3)
    assert r.metrics["R2"] == pytest.approx(0.9538, abs=1e-3)


def test_selection_picks_mm_offset(pairs):
    xs, ys = pairs
    best = select_best(fit_all(xs, ys))
    assert best.method == "mm_offset"


def test_mm_offset_is_monotone_and_bounded(pairs):
    xs, ys = pairs
    r = fit_candidate("mm_offset", xs, ys)
    grid = np.linspace(0, 1, 100)
    out = predict("mm_offset", r.params, grid)
    assert np.all(np.diff(out) > 0), "MM+offset must be strictly monotone"
    assert np.all(out <= r.params["y0"] + r.params["vmax"] + 1e-9)


def test_loo_rmse_sane(pairs):
    xs, ys = pairs
    for r in fit_all(xs, ys):
        assert r.metrics["LOO_RMSE"] >= r.metrics["RMSE"] - 1e-9
