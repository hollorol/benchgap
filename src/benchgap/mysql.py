"""``benchgap sql``: the database as a MySQL script.

All tables are loaded under temporary names and swapped in with one
RENAME TABLE, so readers never see a half-loaded database. Tables, columns,
primary keys and indexes follow the SQLite schema (INTEGER -> BIGINT,
REAL -> DOUBLE, TEXT -> TEXT or VARCHAR(255) where indexed); defaults, CHECK
and foreign-key constraints stay in SQLite, where the pipeline enforces them.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

NEW, OLD = "__new", "__old"


def _q(name: str) -> str:
    return f"`{name}`"


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _indexes(conn: sqlite3.Connection, table: str) -> list[tuple[bool, list[str]]]:
    """(unique, columns) of every index except the primary key's."""
    out = []
    for _seq, name, unique, origin, _partial in conn.execute(f"PRAGMA index_list({_q(table)})"):
        if origin != "pk":
            cols = [r[2] for r in conn.execute(f"PRAGMA index_info({_q(name)})")]
            out.append((bool(unique), cols))
    return out


def create_table(conn: sqlite3.Connection, table: str, name: str) -> str:
    """MySQL CREATE TABLE ``name`` for the SQLite table ``table``."""
    columns = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
    indexes = _indexes(conn, table)
    pk = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
    keyed = set(pk).union(*(cols for _, cols in indexes))
    lines = []
    for _cid, col, decl, notnull, _default, _pk in columns:
        decl = decl.upper()
        if "INT" in decl:
            kind = "BIGINT"
        elif any(t in decl for t in ("REAL", "FLOA", "DOUB")):
            kind = "DOUBLE"
        else:
            kind = "VARCHAR(255)" if col in keyed else "TEXT"
        lines.append(f"  {_q(col)} {kind}{' NOT NULL' if notnull or col in pk else ''}")
    if pk:
        lines.append(f"  PRIMARY KEY ({', '.join(map(_q, pk))})")
    for unique, cols in indexes:
        lines.append(f"  {'UNIQUE KEY' if unique else 'KEY'} ({', '.join(map(_q, cols))})")
    return (
        f"CREATE TABLE {_q(name)} (\n" + ",\n".join(lines) + "\n)"
        " ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_bin;"
    )


def _value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"MySQL has no {v!r}")
        return repr(v)
    if isinstance(v, bytes):
        return f"X'{v.hex()}'"
    # hex literals need no escaping
    return f"CONVERT(X'{str(v).encode('utf-8').hex()}' USING utf8mb4)"


def write_sql(conn: sqlite3.Connection, path: str | Path, batch: int = 500) -> dict[str, int]:
    """Write the MySQL load script; returns the number of rows per table."""
    tables = _tables(conn)
    lines = [
        "-- benchgap database for benchgap.net, written by `benchgap sql`.",
        "-- Loads every table under a temporary name, then swaps all of them in at once.",
        "SET NAMES utf8mb4;",
    ]
    counts = {}
    for t in tables:
        lines += [f"DROP TABLE IF EXISTS {_q(t + NEW)}, {_q(t + OLD)};", create_table(conn, t, t + NEW)]
        rows = conn.execute(f"SELECT * FROM {_q(t)} ORDER BY rowid").fetchall()
        counts[t] = len(rows)
        for i in range(0, len(rows), batch):
            values = ",\n".join(
                "(" + ", ".join(_value(v) for v in r) + ")" for r in rows[i : i + batch]
            )
            lines.append(f"INSERT INTO {_q(t + NEW)} VALUES\n{values};")
        lines.append(f"CREATE TABLE IF NOT EXISTS {_q(t)} LIKE {_q(t + NEW)};")
    lines.append(
        "RENAME TABLE "
        + ", ".join(f"{_q(t)} TO {_q(t + OLD)}, {_q(t + NEW)} TO {_q(t)}" for t in tables)
        + ";"
    )
    lines.append(f"DROP TABLE {', '.join(_q(t + OLD) for t in tables)};")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts
