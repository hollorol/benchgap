"""Build the canonical long-format seed CSV from the Artificial Analysis snapshot.

Reads data/aa_scores.json (leaderboard extracts from AA evaluation pages) and
writes data/seed/scores.csv in long format, one row per measured score:

    model_slug,model_name,benchmark,version,capability,unit,harness,score,
    source_url,retrieved_at

Model slugs are derived deterministically from the AA display names so the
same model on different benchmark pages maps to one database row.

Run from the repo root:  python scripts/build_seed.py
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "data" / "aa_scores.json"
OUT = REPO / "data" / "seed" / "scores.csv"

HARNESS = "artificial-analysis"

# Curated benchmark metadata: AA evaluation page slug ->
# (benchmark name, version, capability)
#
# Capabilities group benchmarks whose scores plausibly calibrate each other;
# cross-capability mappings are never fitted (a model without vision scores
# keeps that gap rather than inheriting one from text benchmarks).
AA_EVAL_META = {
    "terminalbench-2-1": ("terminal-bench", "2.1", "agentic-terminal"),
    "terminalbench-4-0": ("terminal-bench", "4.0", "agentic-terminal"),
    "terminalbench-hard": ("terminal-bench-hard", "1.0", "agentic-terminal"),
    "terminal-bench-science": ("terminal-bench-science", "0.1", "agentic-terminal"),
    "gpqa-diamond": ("gpqa", "diamond", "knowledge"),
    "mmlu-pro": ("mmlu-pro", "1.0", "knowledge"),
    "global-mmlu-lite": ("global-mmlu-lite", "1.0", "knowledge"),
    "humanitys-last-exam": ("hle", "1.0", "knowledge"),
    "critpt": ("critpt", "1.0", "knowledge"),
    "math-500": ("math-500", "1.0", "math"),
    "aime-2025": ("aime-2025", "2025", "math"),
    "livecodebench": ("livecodebench", "1.0", "coding"),
    "scicode": ("scicode", "1.0", "coding"),
    "tau3-banking": ("tau-bank", "3-banking", "agentic-tool"),
    "tau2-bench": ("tau-bench", "2-telecom", "agentic-tool"),
    "automationbench-aa": ("automationbench", "1.0", "agentic-tool"),
    "apex-agents-aa": ("apex-agents", "1.0", "agentic-tool"),
    "aa-analyst-agent": ("analyst-agent", "1.0", "agentic-tool"),
    "enterprise-ops-gym-aa": ("enterprise-ops-gym", "1.0", "agentic-tool"),
    "harvey-lab-aa": ("harvey-lab", "1.0", "agentic-tool"),
    "itbench-aa": ("itbench", "sre", "agentic-tool"),
    "ifbench": ("ifbench", "1.0", "instruction-following"),
    "mmmu-pro": ("mmmu-pro", "1.0", "vision"),
    "gdp-pdf": ("gdp-pdf", "1.0", "vision"),
}


def slugify(name: str) -> str:
    """Deterministic slug from an AA display name, stable across pages."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def main() -> None:
    snapshot = json.loads(SNAPSHOT.read_text())
    rows = []
    skipped = []
    for ev in snapshot["evaluations"]:
        meta = AA_EVAL_META.get(ev["slug"])
        if meta is None:
            skipped.append(ev["slug"])
            continue
        benchmark, version, capability = meta
        for m in ev["models"]:
            if not 0.0 <= m["score"] <= 1.0:
                continue
            rows.append(
                {
                    "model_slug": slugify(m["name"]),
                    "model_name": m["name"],
                    "benchmark": benchmark,
                    "version": version,
                    "capability": capability,
                    "unit": "fraction",
                    "harness": HARNESS,
                    "score": f"{m['score']:.6f}",
                    "source_url": ev["url"],
                    "retrieved_at": (ev.get("retrievedAt") or "")[:10],
                }
            )
    if skipped:
        print(f"warning: no metadata for {skipped}, skipped")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "model_slug",
        "model_name",
        "benchmark",
        "version",
        "capability",
        "unit",
        "harness",
        "score",
        "source_url",
        "retrieved_at",
    ]
    with open(OUT, "w", newline="") as fh:
        import csv

        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    n_models = len({r["model_slug"] for r in rows})
    n_bench = len({(r["benchmark"], r["version"]) for r in rows})
    print(f"wrote {OUT}: {len(rows)} scores, {n_models} models, {n_bench} benchmark versions")


if __name__ == "__main__":
    main()
