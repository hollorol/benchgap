"""The masked-holdout evaluation of the whole pipeline, stored in the database.

The evaluation is the paper's: measured cells are masked, every fitted table is
wiped, the pipeline is fully refit on the reduced data, and the masked cells are
scored against their held-out truth (holdout_eval.py in the paper's artifact).
Its runs are measured artifacts of one data snapshot, so they are not recomputed
daily; holdout_results.json ships with the package (like the harness-tax seed
registry) and ``benchgap holdout`` re-stores it after every database rebuild.
"""
from __future__ import annotations

import json
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Optional


def load_results(path: Optional[Path] = None) -> dict:
    """The evaluation's results: the shipped condensed JSON, or a path."""
    if path is not None:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = (resources.files(__package__) / "holdout_results.json").read_text(encoding="utf-8")
    results = json.loads(text)
    if not results.get("runs") or "headline" not in results:
        raise ValueError(f"{path or 'holdout_results.json'}: no runs or headline")
    return results


def store(conn: sqlite3.Connection, path: Optional[Path] = None) -> list[dict]:
    """Replace the holdout_eval table with the results file's runs (plus the
    headline summary row). Returns the stored runs."""
    results = load_results(path)
    conn.execute("DELETE FROM holdout_eval")
    rows = [("headline", "summary", json.dumps(results["headline"]))]
    for run in results["runs"]:
        rows.append((run["run_id"], run["scheme"], json.dumps(run)))
    conn.executemany(
        "INSERT INTO holdout_eval (run_id, scheme, summary_json) VALUES (?, ?, ?)", rows
    )
    conn.commit()
    return results["runs"]


def summary(conn: sqlite3.Connection) -> dict:
    """The stored results: the headline and the runs, parsed. Empty if never stored."""
    out: dict = {"headline": None, "runs": []}
    for run_id, _scheme, summary_json in conn.execute(
        "SELECT run_id, scheme, summary_json FROM holdout_eval ORDER BY id"
    ):
        parsed = json.loads(summary_json)
        if run_id == "headline":
            out["headline"] = parsed
        else:
            out["runs"].append(parsed)
    return out
