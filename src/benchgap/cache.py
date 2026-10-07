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

    def save(self) -> None:
        if self.file:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.used))

    def stats(self) -> str:
        reused = sum(k in self.earlier for k in self.used)
        return f"{reused} of {len(self.used)} fits reused" if self.file else ""
