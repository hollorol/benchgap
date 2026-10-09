"""SQLite schema and small data-access helpers.

All scores are stored as fractions in [0, 1]. Reports render percent.

Design notes for the probabilistic future:
- ``mappings.params_json`` holds the fitted parameters of a mapping; a
  probabilistic method can additionally store posterior summaries there.
- ``scores.prediction_json`` holds prediction metadata (input score,
  extrapolation flag); a probabilistic gapfill can put credible intervals
  there and/or fill the nullable ``ci95_lo`` / ``ci95_hi`` columns.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmarks (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    capability TEXT NOT NULL DEFAULT 'general',
    label      TEXT,
    featured   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS benchmark_versions (
    id           INTEGER PRIMARY KEY,
    benchmark_id INTEGER NOT NULL REFERENCES benchmarks(id),
    version      TEXT NOT NULL,
    harness      TEXT NOT NULL DEFAULT 'unknown',
    unit         TEXT NOT NULL DEFAULT 'fraction',
    source_url   TEXT,
    UNIQUE (benchmark_id, version, harness)
);

CREATE TABLE IF NOT EXISTS models (
    id      INTEGER PRIMARY KEY,
    slug    TEXT NOT NULL UNIQUE,
    name    TEXT,
    release TEXT
);

CREATE TABLE IF NOT EXISTS mappings (
    id              INTEGER PRIMARY KEY,
    from_version_id INTEGER NOT NULL REFERENCES benchmark_versions(id),
    to_version_id   INTEGER NOT NULL REFERENCES benchmark_versions(id),
    method          TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    metrics_json    TEXT NOT NULL,
    n_points        INTEGER NOT NULL,
    train_range_json TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (from_version_id, to_version_id, method)
);

CREATE TABLE IF NOT EXISTS mapping_points (
    mapping_id INTEGER NOT NULL REFERENCES mappings(id) ON DELETE CASCADE,
    model_id   INTEGER NOT NULL REFERENCES models(id),
    x          REAL NOT NULL,
    y          REAL NOT NULL,
    PRIMARY KEY (mapping_id, model_id)
);

-- the selected fit of each pair of versions of different capabilities: for the
-- cross-domain view only, never used for estimates (fit.fit_cross_mappings)
CREATE TABLE IF NOT EXISTS cross_mappings (
    id              INTEGER PRIMARY KEY,
    from_version_id INTEGER NOT NULL REFERENCES benchmark_versions(id),
    to_version_id   INTEGER NOT NULL REFERENCES benchmark_versions(id),
    method          TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    metrics_json    TEXT NOT NULL,
    n_points        INTEGER NOT NULL,
    passes          INTEGER NOT NULL,  -- 1 if it passes the quality gate
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (from_version_id, to_version_id)
);

-- each target's best fit from several benchmarks of any capability, with each model's
-- leave-one-out prediction: for the multivariate view only, never used for estimates
-- (multivariate.fit_cross_multimappings)
CREATE TABLE IF NOT EXISTS cross_multi_mappings (
    id                       INTEGER PRIMARY KEY,
    to_version_id            INTEGER NOT NULL UNIQUE REFERENCES benchmark_versions(id),
    method                   TEXT NOT NULL,
    feature_version_ids_json TEXT NOT NULL,
    params_json              TEXT NOT NULL,
    metrics_json             TEXT NOT NULL,
    n_points                 INTEGER NOT NULL,
    points_json              TEXT NOT NULL,  -- [[model_id, measured, leave-one-out prediction], ...]
    alone_loo                REAL,           -- LOO RMSE of the best of its features alone, on the same models
    passes                   INTEGER NOT NULL,  -- 1 if it passes the quality gate
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS multi_mappings (
    id                     INTEGER PRIMARY KEY,
    to_version_id          INTEGER NOT NULL REFERENCES benchmark_versions(id),
    method                 TEXT NOT NULL,
    feature_version_ids_json TEXT NOT NULL,
    params_json           TEXT NOT NULL,
    metrics_json          TEXT NOT NULL,
    n_points              INTEGER NOT NULL,
    train_ranges_json     TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scores (
    id               INTEGER PRIMARY KEY,
    model_id         INTEGER NOT NULL REFERENCES models(id),
    version_id       INTEGER NOT NULL REFERENCES benchmark_versions(id),
    value            REAL NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('measured', 'gapfilled')),
    mapping_id       INTEGER REFERENCES mappings(id),
    multi_mapping_id INTEGER REFERENCES multi_mappings(id) ON DELETE SET NULL,
    prediction_json  TEXT,
    ci95_lo          REAL,
    ci95_hi          REAL,
    retrieved_at    TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (model_id, version_id, source, mapping_id)
);

CREATE INDEX IF NOT EXISTS idx_scores_model ON scores(model_id);
CREATE INDEX IF NOT EXISTS idx_scores_version ON scores(version_id);
"""

# the harness-tax analysis layer (harness_tax.py): the same benchmark's measured
# scores under different harnesses, never used for estimates. Kept apart from
# SCHEMA so an existing database can add the tables alone (ensured on every run).
HARNESS_TAX_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_tax_families (
    family_id     TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    capability    TEXT NOT NULL,
    tier          TEXT,
    same_item_set TEXT NOT NULL,
    status        TEXT NOT NULL,
    pair_type     TEXT,
    audit_note    TEXT,
    origin        TEXT NOT NULL DEFAULT 'seed',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS harness_tax_pairs (
    id            INTEGER PRIMARY KEY,
    family_id     TEXT NOT NULL REFERENCES harness_tax_families(family_id) ON DELETE CASCADE,
    version_a_id  INTEGER NOT NULL REFERENCES benchmark_versions(id),
    version_b_id  INTEGER NOT NULL REFERENCES benchmark_versions(id),
    pair_type     TEXT NOT NULL,
    n_models      INTEGER NOT NULL,
    mean_abs_pp   REAL,
    median_abs_pp REAL,
    max_abs_pp    REAL,
    share_gt_5    REAL,
    share_gt_10   REAL,
    kendall_tau   REAL,
    n_rank_flips  INTEGER NOT NULL,
    n_positive    INTEGER NOT NULL,
    n_negative    INTEGER NOT NULL,
    sign_p        REAL,
    directionality REAL,
    low_overlap   INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (family_id, version_a_id, version_b_id)
);

CREATE TABLE IF NOT EXISTS harness_tax_deltas (
    pair_id  INTEGER NOT NULL REFERENCES harness_tax_pairs(id) ON DELETE CASCADE,
    model_id INTEGER NOT NULL REFERENCES models(id),
    score_a  REAL NOT NULL,
    score_b  REAL NOT NULL,
    delta_pp REAL NOT NULL,
    PRIMARY KEY (pair_id, model_id)
);

-- the one row of aggregates the API serves (computed once, not recomposed from pairs)
CREATE TABLE IF NOT EXISTS harness_tax_aggregates (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    aggregates_json TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

HOLDOUT_SCHEMA = """
-- the masked-holdout evaluation of the whole pipeline (holdout.py, benchgap holdout):
-- one row per run of the shipped results file, plus the 'headline' summary row.
-- The results are measured artifacts of a data snapshot, not recomputed daily;
-- benchgap holdout re-stores the shipped summary after every database rebuild.
CREATE TABLE IF NOT EXISTS holdout_eval (
    id           INTEGER PRIMARY KEY,
    run_id       TEXT NOT NULL UNIQUE,
    scheme       TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SCHEMA = SCHEMA + HARNESS_TAX_SCHEMA + HOLDOUT_SCHEMA


def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    """Open (creating if needed) the benchgap database with foreign keys on, or an existing one read-only."""
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


MIGRATIONS = [
    ("benchmarks", "capability", "ALTER TABLE benchmarks ADD COLUMN capability TEXT NOT NULL DEFAULT 'general'"),
    ("benchmarks", "label", "ALTER TABLE benchmarks ADD COLUMN label TEXT"),
    ("benchmarks", "featured", "ALTER TABLE benchmarks ADD COLUMN featured INTEGER NOT NULL DEFAULT 0"),
    ("benchmark_versions", "unit", "ALTER TABLE benchmark_versions ADD COLUMN unit TEXT NOT NULL DEFAULT 'fraction'"),
    ("scores", "multi_mapping_id", "ALTER TABLE scores ADD COLUMN multi_mapping_id INTEGER REFERENCES multi_mappings(id) ON DELETE SET NULL"),
]


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(ddl)
    conn.commit()


# --- lookup / upsert helpers -------------------------------------------------


def get_or_create_benchmark(
    conn: sqlite3.Connection,
    name: str,
    capability: str = "general",
    label: Optional[str] = None,
    featured: bool = False,
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO benchmarks (name, capability) VALUES (?, ?)",
        (name, capability),
    )
    conn.execute(
        "UPDATE benchmarks SET capability = ? WHERE name = ? AND capability = 'general'",
        (capability, name),
    )
    if label:
        conn.execute("UPDATE benchmarks SET label = ? WHERE name = ?", (label, name))
    if featured:
        conn.execute("UPDATE benchmarks SET featured = 1 WHERE name = ?", (name,))
    return conn.execute(
        "SELECT id FROM benchmarks WHERE name = ?", (name,)
    ).fetchone()["id"]


def get_or_create_version(
    conn: sqlite3.Connection,
    benchmark: str,
    version: str,
    harness: str = "unknown",
    source_url: Optional[str] = None,
    capability: str = "general",
    unit: str = "fraction",
    label: Optional[str] = None,
    featured: bool = False,
) -> int:
    bid = get_or_create_benchmark(conn, benchmark, capability, label, featured)
    conn.execute(
        "INSERT OR IGNORE INTO benchmark_versions"
        " (benchmark_id, version, harness, unit, source_url) VALUES (?, ?, ?, ?, ?)",
        (bid, version, harness, unit, source_url),
    )
    return conn.execute(
        "SELECT id FROM benchmark_versions"
        " WHERE benchmark_id = ? AND version = ? AND harness = ?",
        (bid, version, harness),
    ).fetchone()["id"]


def get_or_create_model(
    conn: sqlite3.Connection, slug: str, name: str, release: Optional[str] = None
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO models (slug, name, release) VALUES (?, ?, ?)",
        (slug, name, release),
    )
    row = conn.execute("SELECT id, name, release FROM models WHERE slug = ?", (slug,)).fetchone()
    if (name and row["name"] is None) or (release and row["release"] is None):
        conn.execute(
            "UPDATE models SET name = COALESCE(?, name), release = COALESCE(?, release)"
            " WHERE id = ?",
            (name, release, row["id"]),
        )
    return row["id"]


def set_measured_score(
    conn: sqlite3.Connection,
    model_id: int,
    version_id: int,
    value: float,
    retrieved_at: Optional[str] = None,
) -> None:
    """Insert or update the single measured score for a (model, version)."""
    row = conn.execute(
        "SELECT id FROM scores"
        " WHERE model_id = ? AND version_id = ? AND source = 'measured'",
        (model_id, version_id),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO scores (model_id, version_id, value, source, retrieved_at)"
            " VALUES (?, ?, ?, 'measured', ?)",
            (model_id, version_id, float(value), retrieved_at),
        )
    else:
        conn.execute(
            "UPDATE scores SET value = ?, retrieved_at = ? WHERE id = ?",
            (float(value), retrieved_at, row["id"]),
        )


def version_label(row: sqlite3.Row) -> str:
    """Human-readable identifier for a benchmark version row."""
    return f"{row['benchmark']}/{row['version']}@{row['harness']}"


def parse_version_spec(conn: sqlite3.Connection, spec: str) -> sqlite3.Row:
    """Resolve a version spec of the form benchmark/version[@harness].

    The harness part is optional when it is unambiguous.
    """
    parts = spec.split("/")
    if len(parts) != 2:
        raise ValueError(f"version spec must be 'benchmark/version[@harness]', got {spec!r}")
    bench, rest = parts
    if "@" in rest:
        version, harness = rest.split("@", 1)
    else:
        version, harness = rest, None
    sql = (
        "SELECT v.*, b.name AS benchmark FROM benchmark_versions v"
        " JOIN benchmarks b ON b.id = v.benchmark_id"
        " WHERE b.name = ? AND v.version = ?"
    )
    args: list[Any] = [bench, version]
    if harness is not None:
        sql += " AND v.harness = ?"
        args.append(harness)
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        raise ValueError(f"no benchmark version matching {spec!r}")
    if len(rows) > 1:
        labels = ", ".join(version_label(r) for r in rows)
        raise ValueError(f"{spec!r} is ambiguous, use @harness: {labels}")
    return rows[0]


def load_json(text: str) -> Any:
    return json.loads(text)
