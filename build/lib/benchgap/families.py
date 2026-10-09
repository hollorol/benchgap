"""The benchmark family registry of the harness-tax analysis.

A family groups the benchmark versions that measure the same benchmark under
different harnesses or run protocols (families.json, seeded by hand; candidates
are auto-detected by normalized base name and enter as 'candidate' status,
to be promoted into the seed only after review). The registry is pure data:
it resolves nothing against the database, so it stays testable on its own.

Fields per family (harness_tax.HarnessTax writes them to harness_tax_families):
family_id, label, capability, tier, same_item_set, status, pair_type,
audit_note, versions[] (name, harness as registered, role).
"""
from __future__ import annotations

import json
import re
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Optional

TIERS = ("agentic", "knowledge_tool_free", "protocol_layer")
ITEM_SETS = ("verified", "needs_audit", "weak_alignment")
STATUSES = ("active", "candidate")


def load_families(path: Optional[Path] = None) -> list[dict]:
    """The registry: the seed JSON (the package's families.json, or a path)."""
    if path is not None:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = (resources.files(__package__) / "families.json").read_text(encoding="utf-8")
    families = json.loads(text)["families"]
    validate(families)
    return families


def validate(families: list[dict]) -> None:
    """Raise ValueError on a registry that could not be paired faithfully."""
    seen: set[str] = set()
    for f in families:
        for field in ("family_id", "label", "capability", "same_item_set", "status", "versions"):
            if not f.get(field):
                raise ValueError(f"family {f.get('family_id', '?')} misses {field}")
        if f["family_id"] in seen:
            raise ValueError(f"duplicate family_id {f['family_id']!r}")
        seen.add(f["family_id"])
        if f["same_item_set"] not in ITEM_SETS:
            raise ValueError(f"{f['family_id']}: same_item_set must be one of {ITEM_SETS}, got {f['same_item_set']!r}")
        if f["status"] not in STATUSES:
            raise ValueError(f"{f['family_id']}: status must be one of {STATUSES}, got {f['status']!r}")
        if len(f["versions"]) < 2:
            raise ValueError(f"{f['family_id']}: a family pairs at least two versions")
        names = [v["name"] for v in f["versions"]]
        if len(set(names)) != len(names):
            raise ValueError(f"{f['family_id']}: duplicate version names {names}")


# --- auto-detection -----------------------------------------------------------

# names normalize to a common base: 'aa-terminal-bench21', 'vals-terminal-bench21'
# and 'terminalbench21' all become 'terminalbench21'; suffix tokens are stripped
# whole ('livecodebench-v6' and 'livecodebench-pro' join 'livecodebench')
_PREFIXES = ("aa-", "vals-", "val-", "vals")
_SUFFIX_TOKENS = {"v6", "pro", "arcee", "python", "notools", "withtools", "6x", "verifier"}
_EMBEDDED_SUFFIXES = ("notools", "withtools", "arcee")   # inside a token: 'hlenotools' -> 'hle'


def normalize_name(name: str) -> str:
    """The base name a family groups by: prefixes stripped, suffix tokens dropped, separators collapsed."""
    s = name.lower().strip().replace("_", "-").replace(" ", "-")
    for prefix in _PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    tokens = []
    for t in s.split("-"):
        for suffix in _EMBEDDED_SUFFIXES:
            if t.endswith(suffix) and len(t) > len(suffix):
                t = t[:-len(suffix)]
        if t and t not in _SUFFIX_TOKENS:
            tokens.append(t)
    return "".join(tokens)


def detect_families(conn: sqlite3.Connection, families: list[dict]) -> list[dict]:
    """Candidate families the seed does not cover: benchmark names grouped by normalized base name.

    Only a group of at least two benchmarks whose names none of the seed's families
    list enters, as status 'candidate' with same_item_set 'weak_alignment': auto-detection
    cannot know whether the items are shared, so nothing it finds is trusted before review.
    """
    covered = {v["name"] for f in families for v in f["versions"]}
    rows = conn.execute(
        "SELECT b.name, b.capability FROM benchmarks b ORDER BY b.name"
    ).fetchall()
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(normalize_name(row["name"]), []).append(row)
    candidates = []
    for base, rows in sorted(groups.items()):
        names = [r["name"] for r in rows]
        if len(names) < 2 or any(n in covered for n in names):
            continue
        label = base.replace("-", " ").strip().title() or base
        capabilities = {r["capability"] for r in rows}
        candidates.append({
            "family_id": base,
            "label": label,
            "capability": sorted(capabilities)[0],
            "tier": None,
            "same_item_set": "weak_alignment",
            "status": "candidate",
            "audit_note": "Auto-detected by normalized base name; review the item set before promoting to the seed.",
            "versions": [{"name": n, "harness": None} for n in names],
            "origin": "auto",
        })
    return candidates
