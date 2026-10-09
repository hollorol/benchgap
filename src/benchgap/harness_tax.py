"""The harness-tax analysis: how much the same benchmark disagrees about the
same models when measured under different harnesses or run protocols.

It is a read-only layer over the measured scores: no delta ever involves a
gapfilled score, and nothing here feeds the mappings or the estimates - the
harness tax explains *why* calibrations are harness-specific (README, Harness
tax). One benchmark family (families.py) groups the versions compared; within
a family every unordered version pair is a pair, in the registry's order, so
daily runs stay comparable.

Deltas are in percentage points: delta = (score_a - score_b) * 100, a minus b
in the registry's order. A pair with a systematic sign of delta (most models
agreeing on which harness scores higher) is a harness tax; a pair with only a
spread of deltas is noise. The metrics (share over thresholds, Kendall tau-b,
rank flips, a sign test against p = 0.5, directionality) separate the two.

`analyze` is pure computation and returns the whole analysis document;
`store`/`write_json`/`append_history` persist it (the tables, data/harness_tax.json,
and the append-only history), and `run` does all of it for the CLI.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scipy import stats

from .db import HARNESS_TAX_SCHEMA
from .families import detect_families, load_families

MIN_OVERLAP = 5          # below this a pair is stored but flagged low_overlap
OUTLIER_PP = 20.0         # a per-model delta beyond this goes to the audit queue
SCORE_EPS = 1e-9          # measured scores are fractions in [0, 1]


class HarnessTaxError(Exception):
    """A version the registry pairs has no measured scores: the run fails loudly."""


# --- schema -------------------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(HARNESS_TAX_SCHEMA)


# --- one pair -----------------------------------------------------------------

def _measured(conn: sqlite3.Connection, version_id: int) -> dict[int, sqlite3.Row]:
    """The version's measured scores by model: {model_id: {value, retrieved_at}}."""
    rows = conn.execute(
        "SELECT model_id, value, retrieved_at FROM scores"
        " WHERE version_id = ? AND source = 'measured' ORDER BY retrieved_at, id",
        (version_id,),
    ).fetchall()
    return {r["model_id"]: r for r in rows}


def _rank_flips(score_a: list[float], score_b: list[float]) -> int:
    """Discordant model pairs: i above j in one harness, j above i in the other."""
    n = 0
    for i in range(len(score_a)):
        for j in range(i + 1, len(score_a)):
            if (score_a[i] - score_a[j]) * (score_b[i] - score_b[j]) < 0:
                n += 1
    return n


def pair_metrics(deltas: list[dict]) -> dict:
    """The metrics of one pair's per-model deltas (each {model_id, score_a, score_b, delta_pp}).

    A pair with no common models (both versions measured, on none of the same
    models) keeps its row with null metrics rather than vanishing: it is still
    a pair, only an unmeasurable one.
    """
    n = len(deltas)
    if n == 0:
        return {
            "n": 0, "mean_abs_pp": None, "median_abs_pp": None, "max_abs_pp": None,
            "share_gt_5": None, "share_gt_10": None, "kendall_tau": None, "n_rank_flips": 0,
            "n_positive": 0, "n_negative": 0, "sign_p": None, "directionality": None,
        }
    abs_pp = [abs(d["delta_pp"]) for d in deltas]
    a = [d["score_a"] for d in deltas]
    b = [d["score_b"] for d in deltas]
    pos = sum(1 for d in deltas if d["delta_pp"] > 0)
    neg = sum(1 for d in deltas if d["delta_pp"] < 0)
    sign_n = pos + neg
    return {
        "n": n,
        "mean_abs_pp": sum(abs_pp) / n,
        "median_abs_pp": sorted(abs_pp)[n // 2] if n % 2 else (sorted(abs_pp)[n // 2 - 1] + sorted(abs_pp)[n // 2]) / 2,
        "max_abs_pp": max(abs_pp),
        "share_gt_5": sum(1 for x in abs_pp if x > 5) / n,
        "share_gt_10": sum(1 for x in abs_pp if x > 10) / n,
        "kendall_tau": float(stats.kendalltau(a, b).statistic) if n >= 2 else None,
        "n_rank_flips": _rank_flips(a, b),
        "n_positive": pos,
        "n_negative": neg,
        "sign_p": float(stats.binomtest(min(pos, neg), sign_n, 0.5).pvalue) if sign_n else None,
        "directionality": max(pos, neg) / sign_n if sign_n else None,
    }


def _version(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    """The one benchmark version row of a registry name; more than one is an ambiguity."""
    rows = conn.execute(
        "SELECT v.id, v.version, v.harness, v.source_url, b.name, b.capability, b.label"
        " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        " WHERE b.name = ? ORDER BY v.version",
        (name,),
    ).fetchall()
    if not rows:
        raise HarnessTaxError(f"benchmark {name!r} is not in the database")
    if len(rows) > 1:
        keys = ", ".join(f"{r['name']}/{r['version']}@{r['harness']}" for r in rows)
        raise HarnessTaxError(f"benchmark {name!r} has several versions ({keys}); the registry lists bare names")
    return rows[0]


def _pairs_of_family(conn: sqlite3.Connection, family: dict, min_overlap: int) -> tuple[list[dict], list[str]]:
    """Every version pair of one family, with its per-model deltas and metrics."""
    warnings: list[str] = []
    versions = []
    for spec in family["versions"]:
        row = _version(conn, spec["name"])
        if spec.get("harness") and spec["harness"] != row["harness"]:
            warnings.append(
                f"{family['family_id']}: {spec['name']} is registered with harness"
                f" {spec['harness']!r} but the database says {row['harness']!r}; the database wins"
            )
        versions.append((spec, row))
    pairs = []
    for i in range(len(versions)):
        for j in range(i + 1, len(versions)):
            spec_a, va = versions[i]
            spec_b, vb = versions[j]
            a = _measured(conn, va["id"])
            b = _measured(conn, vb["id"])
            if not a or not b:
                raise HarnessTaxError(
                    f"{family['family_id']}: {va['name']} and {vb['name']} are paired,"
                    f" but {(va['name'] if not a else vb['name'])} has no measured scores"
                )
            common = sorted(a.keys() & b.keys())
            deltas = []
            for model_id in common:
                sa, sb = float(a[model_id]["value"]), float(b[model_id]["value"])
                if not (-SCORE_EPS <= sa <= 1 + SCORE_EPS and -SCORE_EPS <= sb <= 1 + SCORE_EPS):
                    warnings.append(
                        f"{family['family_id']}: {va['name']} vs {vb['name']} skipped:"
                        f" a measured score outside [0, 1] (model {model_id})"
                    )
                    deltas = None
                    break
                deltas.append({
                    "model_id": model_id,
                    "score_a": sa,
                    "score_b": sb,
                    "delta_pp": (sa - sb) * 100,
                    "retrieved_a": a[model_id]["retrieved_at"],
                    "retrieved_b": b[model_id]["retrieved_at"],
                })
            if deltas is None:
                continue
            metrics = pair_metrics(deltas)
            pair_type = (
                "protocol_variant" if family.get("pair_type") == "protocol_variant"
                else "cross_harness" if va["harness"] != vb["harness"]
                else "protocol_variant"
            )
            pairs.append({
                "family_id": family["family_id"],
                "capability": family["capability"],
                "tier": family.get("tier"),
                "pair_type": pair_type,
                "same_item_set": family["same_item_set"],
                "status": family["status"],
                "low_overlap": metrics["n"] < min_overlap,
                "a": {
                    "id": va["id"], "name": va["name"], "version": va["version"], "harness": va["harness"],
                    "source_url": va["source_url"], "role": spec_a.get("role"),
                },
                "b": {
                    "id": vb["id"], "name": vb["name"], "version": vb["version"], "harness": vb["harness"],
                    "source_url": vb["source_url"], "role": spec_b.get("role"),
                },
                "metrics": metrics,
                "deltas": deltas,
            })
    return pairs, warnings


# --- aggregates ---------------------------------------------------------------

def _pooled(pairs: list[dict]) -> dict:
    n = sum(p["metrics"]["n"] for p in pairs)
    return {
        "n_pairs": len(pairs),
        "n_model_pairs": n,
        "pooled_mean_abs_pp": sum(p["metrics"]["mean_abs_pp"] * p["metrics"]["n"] for p in pairs) / n if n else None,
    }


def _aggregate(pairs: list[dict], by: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for p in pairs:
        groups.setdefault(p[by], []).append(p)
    return {key: _pooled(group) for key, group in sorted(groups.items()) if key is not None}


def _reportable(pairs: list[dict], verified_only: bool) -> list[dict]:
    """The pairs aggregates are computed over: active families, enough overlap, a known item set."""
    out = [
        p for p in pairs
        if p["status"] == "active"
        and not p["low_overlap"]
        and p["same_item_set"] != "weak_alignment"
        and (p["same_item_set"] == "verified" if verified_only else True)
    ]
    return out


def aggregates(pairs: list[dict]) -> dict:
    """Tier/capability/family aggregates over all reportable pairs, plus the verified-only
    headline set whose tier ratio is the analysis's headline number."""
    tiers = _aggregate(_reportable(pairs, verified_only=False), "tier")
    by_family = _aggregate(_reportable(pairs, verified_only=False), "family_id")
    by_capability = _aggregate(_reportable(pairs, verified_only=False), "capability")
    headline_tiers = _aggregate(_reportable(pairs, verified_only=True), "tier")
    agentic = headline_tiers.get("agentic", {}).get("pooled_mean_abs_pp")
    tool_free = headline_tiers.get("knowledge_tool_free", {}).get("pooled_mean_abs_pp")
    return {
        "by_tier": tiers,
        "by_capability": by_capability,
        "by_family": by_family,
        "headline": {
            "by_tier": headline_tiers,
            "ratio": agentic / tool_free if agentic is not None and tool_free is not None else None,
        },
    }


# --- the whole analysis -------------------------------------------------------

def analyze(conn: sqlite3.Connection, families: Optional[list[dict]] = None,
            min_overlap: int = MIN_OVERLAP) -> dict:
    """The full analysis document of the database's current measured scores.

    Raises HarnessTaxError (fail loudly) if a paired version has no measured scores.
    """
    ensure_schema(conn)
    families = families if families is not None else load_families()
    candidates = detect_families(conn, families)
    warnings: list[str] = []
    pairs: list[dict] = []
    for family in families + candidates:
        family_pairs, family_warnings = _pairs_of_family(conn, family, min_overlap)
        pairs.extend(family_pairs)
        warnings.extend(family_warnings)
    outliers = [
        {
            "family_id": p["family_id"],
            "pair": f"{p['a']['name']} vs {p['b']['name']}",
            "model_id": d["model_id"],
            "delta_pp": d["delta_pp"],
            "score_a": d["score_a"],
            "score_b": d["score_b"],
            "a": {k: p["a"][k] for k in ("name", "harness", "source_url")},
            "b": {k: p["b"][k] for k in ("name", "harness", "source_url")},
        }
        for p in pairs for d in p["deltas"] if abs(d["delta_pp"]) > OUTLIER_PP
    ]
    for family in families + candidates:
        warnings.extend(f"{family['family_id']}: {note}" for note in [family.get("audit_note")] if note)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_retrieved_at": conn.execute(
            "SELECT MAX(retrieved_at) FROM scores WHERE source = 'measured'"
        ).fetchone()[0],
        "measured_only": True,
        "families": families + candidates,
        "pairs": pairs,
        "aggregates": aggregates(pairs),
        "audit": {"outliers": outliers, "warnings": warnings, "same_item_sets": ["verified", "needs_audit", "weak_alignment"]},
    }


# --- persistence --------------------------------------------------------------

def store(conn: sqlite3.Connection, analysis: dict) -> None:
    """Write the analysis into its tables (replacing the previous run's rows)."""
    conn.execute("DELETE FROM harness_tax_families")
    conn.execute("DELETE FROM harness_tax_aggregates")
    conn.execute(
        "INSERT INTO harness_tax_aggregates (id, aggregates_json) VALUES (1, ?)",
        (json.dumps(analysis["aggregates"]),),
    )
    for f in analysis["families"]:
        conn.execute(
            "INSERT INTO harness_tax_families"
            " (family_id, label, capability, tier, same_item_set, status, pair_type, audit_note, origin)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f["family_id"], f["label"], f["capability"], f.get("tier"), f["same_item_set"],
             f["status"], f.get("pair_type"), f.get("audit_note"), f.get("origin", "seed")),
        )
    for p in analysis["pairs"]:
        cur = conn.execute(
            "INSERT INTO harness_tax_pairs"
            " (family_id, version_a_id, version_b_id, pair_type, n_models, mean_abs_pp, median_abs_pp,"
            " max_abs_pp, share_gt_5, share_gt_10, kendall_tau, n_rank_flips, n_positive, n_negative,"
            " sign_p, directionality, low_overlap)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (p["family_id"], p["a"]["id"], p["b"]["id"], p["pair_type"], p["metrics"]["n"],
             p["metrics"]["mean_abs_pp"], p["metrics"]["median_abs_pp"], p["metrics"]["max_abs_pp"],
             p["metrics"]["share_gt_5"], p["metrics"]["share_gt_10"], p["metrics"]["kendall_tau"],
             p["metrics"]["n_rank_flips"], p["metrics"]["n_positive"], p["metrics"]["n_negative"],
             p["metrics"]["sign_p"], p["metrics"]["directionality"], 1 if p["low_overlap"] else 0),
        )
        conn.executemany(
            "INSERT INTO harness_tax_deltas (pair_id, model_id, score_a, score_b, delta_pp) VALUES (?, ?, ?, ?, ?)",
            [(cur.lastrowid, d["model_id"], d["score_a"], d["score_b"], d["delta_pp"]) for d in p["deltas"]],
        )
    conn.commit()


def write_json(analysis: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")


def append_history(analysis: dict, path: Path) -> None:
    """Append this run's per-pair stats to the history file; history is never rewritten."""
    with Path(path).open("a", encoding="utf-8") as fh:
        for p in analysis["pairs"]:
            fh.write(json.dumps({
                "date": analysis["data_retrieved_at"] or analysis["generated_at"],
                "pair_id": f"{p['family_id']}:{p['a']['name']}|{p['b']['name']}",
                "family_id": p["family_id"],
                "pair": f"{p['a']['name']} vs {p['b']['name']}",
                "n": p["metrics"]["n"],
                "mean_abs_pp": p["metrics"]["mean_abs_pp"],
                "kendall_tau": p["metrics"]["kendall_tau"],
            }) + "\n")


def run(conn: sqlite3.Connection, families: Optional[list[dict]] = None,
        min_overlap: int = MIN_OVERLAP, output: Optional[Path] = None,
        history: Optional[Path] = None) -> dict:
    """Analyze, store, write the JSON and append the history; returns the analysis."""
    analysis = analyze(conn, families, min_overlap)
    store(conn, analysis)
    if output is not None:
        write_json(analysis, output)
    if history is not None:
        append_history(analysis, history)
    return analysis
