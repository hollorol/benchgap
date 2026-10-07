"""Fit results remembered between runs (``fit --cache DIR``, ``multifit --cache DIR``).

A fit is a pure function of its method and its training data, so a result is
stored under a hash of exactly those (the arrays' values, in order). A run
whose data changed in a few places refits only those, and its results are the
same as fitting everything again. Each run keeps only the entries it used, so
the file never outgrows one build.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import numpy as np


def fit_key(*parts) -> str:
    """The hash of a fit's inputs: strings (e.g. the method) and arrays of numbers."""
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, str):
            h.update(b"s" + p.encode())
        else:
            a = np.ascontiguousarray(p, dtype=float)
            h.update(b"a" + repr(a.shape).encode() + a.tobytes())
    return h.hexdigest()


class FitCache:
    """``<dir>/<name>.json``: earlier results by fit_key; no directory, no cache."""

    def __init__(self, directory: Optional[str | Path], name: str):
        self.file = Path(directory) / f"{name}.json" if directory else None
        self.earlier: dict = {}
        if self.file and self.file.exists():
            try:
                self.earlier = json.loads(self.file.read_text())
            except ValueError:  # a damaged cache is an empty one
                pass
        self.used: dict = {}

    def __contains__(self, key: str) -> bool:
        return key in self.used or key in self.earlier

    def get(self, key: str, compute):
        """The result under key: this run's, an earlier run's, or compute()'s; kept for the next run."""
        if key not in self.used:
            self.used[key] = self.earlier[key] if key in self.earlier else compute()
        return self.used[key]

    @classmethod
    def of(cls, earlier: dict) -> "FitCache":
        """A cache over earlier results (in a worker process: the parent saves what it used)."""
        cache = cls(None, "")
        cache.earlier = earlier
        return cache

    def save(self) -> None:
        if self.file:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.used))

    def stats(self) -> str:
        """How many fits were reused, as " (N of M fits reused)"; "" without a cache directory."""
        reused = sum(k in self.earlier for k in self.used)
        return f" ({reused} of {len(self.used)} fits reused)" if self.file else ""
