"""Tests for the MySQL load script (benchgap sql)."""
from __future__ import annotations

from benchgap.mysql import _tables, create_table, write_sql


def test_write_sql(gapfilled_db, tmp_path):
    out = tmp_path / "benchgap.sql"
    counts = write_sql(gapfilled_db, out)
    assert set(counts) == set(_tables(gapfilled_db))
    for table, n in counts.items():
        assert n == gapfilled_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    sql = out.read_text(encoding="utf-8")
    # loaded under temporary names, then swapped in with a single RENAME
    rename = [line for line in sql.splitlines() if line.startswith("RENAME TABLE")]
    assert len(rename) == 1
    for table in counts:
        assert f"`{table}__new` TO `{table}`" in rename[0]
        assert f"INSERT INTO `{table}__new` VALUES" in sql or counts[table] == 0
    # strings are hex literals, so nothing from the data appears unescaped
    assert "'measured'" not in sql


def test_create_table_keeps_columns_and_keys(gapfilled_db):
    ddl = create_table(gapfilled_db, "scores", "scores")
    for col in gapfilled_db.execute("PRAGMA table_info(scores)"):
        assert f"`{col[1]}`" in ddl
    assert "PRIMARY KEY (`id`)" in ddl
    assert "UNIQUE KEY (`model_id`, `version_id`, `source`, `mapping_id`)" in ddl
    # MySQL cannot index TEXT: indexed text columns become VARCHAR
    assert "`source` VARCHAR(255) NOT NULL" in ddl
    assert "`value` DOUBLE NOT NULL" in ddl
    # `release` is a reserved word in MySQL
    assert "`release` TEXT" in create_table(gapfilled_db, "models", "models")
