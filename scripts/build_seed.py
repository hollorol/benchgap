"""Build the canonical long-format seed CSV from the BenchLM data export.

Reads the BenchLM website data files (https://benchlm.ai/data, CC BY-NC 4.0,
"Data from BenchLM.ai") saved in data/benchlm/ and writes data/seed/scores.csv
in long format, one row per measured score:

    model_slug,model_name,release,benchmark,version,label,featured,capability,
    unit,harness,score,source_url,retrieved_at

models.json carries each model's measured benchmark results (BenchLM's own
generated estimates are not in it); benchmarks.json names and describes each
benchmark. Every BenchLM benchmark key becomes one benchmark version. Scores
that are percentages, higher-is-better, are stored as fractions; Elo-style
ratings, signed indexes, lower-is-better rates and counts are left out, since
they cannot be calibrated against fraction-scale benchmarks.

Run from the repo root:
    python scripts/build_seed.py --fetch   # download data/benchlm/*.json, then build
    python scripts/build_seed.py           # build from the saved files
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "data" / "benchlm"
OUT = REPO / "data" / "seed" / "scores.csv"
SOURCE = "https://benchlm.ai/data"
FILES = ("models.json", "benchmarks.json")

# The version of every benchmark: a BenchLM key already names one version
# (terminalBench21, terminalBench4), and the board holds its current results.
VERSION = "current"

# BenchLM category -> benchgap capability. Calibrations never cross
# capabilities, so a model never inherits a vision score from text benchmarks.
CAPABILITIES = {
    "agentic": "agentic-tool",
    "coding": "coding",
    "math": "math",
    "knowledge": "knowledge",
    "reasoning": "knowledge",
    "instructionFollowing": "instruction-following",
    "multimodalGrounded": "vision",
    "multilingual": "multilingual",
    "korean": "multilingual",
}
# Benchmarks whose name puts them in a narrower capability than their category.
CAPABILITY_BY_NAME = [
    (re.compile(r"terminal-?bench", re.I), "agentic-terminal"),
    (re.compile(r"long[- ]?context|\blcr\b|mrcr|longbench", re.I), "long-context"),
]

# Names of the benchmark keys that benchmarks.json does not describe.
LABELS = {
    "cursorBench31": "CursorBench 3.1",
    "cursorBench32": "CursorBench 3.2",
    "cursorBench40": "CursorBench 4.0",
    "terminalBench3": "Terminal-Bench 3",
    "jevBench14": "JevBench 1.4",
    "jevBench15": "JevBench 1.5",
}
HARNESS_NAMES = {"artificial-analysis": "Artificial Analysis", "vals-ai": "Vals AI"}

# The benchmarks the site started with (Artificial Analysis evaluations), by BenchLM key:
# the leaderboard picker shows them as chips and folds the rest into its "more" lists.
FEATURED = {
    "aaTerminalBench21", "aaTerminalBench4", "terminalBenchHard", "terminalBenchScience",
    "aaGpqaDiamond", "aaMmluPro", "aaGlobalMmluLite", "aaHle", "critpt", "aaMath500", "aaAime2025",
    "aaLiveCodeBench", "aaSciCode", "aaTau3Banking", "tau2Bench", "aaAutomationBench", "apexAgentsAa",
    "aaAnalystAgent", "aaEnterpriseOpsGym", "aaHarveyLab", "aaItbench", "aaIfBench", "aaMmmuPro", "aaGdpPdf",
}

# Metrics that are not a higher-is-better percentage.
NOT_PERCENT = re.compile(
    r"\belo\b|glicko|rating|bradley|trajectory length|turns|latency|seconds|minutes|\bcost\b|"
    r"\busd\b|\bmae\b|hallucination rate|attack success|openness",
    re.I,
)


def fetch() -> None:
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        req = urllib.request.Request(f"{SOURCE}/{name}", headers={"User-Agent": "benchgap (+https://benchgap.net)"})
        with urllib.request.urlopen(req, timeout=120) as r:
            (SNAPSHOT / name).write_bytes(r.read())
        print(f"fetched {SOURCE}/{name}")


def kebab(key: str) -> str:
    """terminalBench21 -> terminal-bench21."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", key).lower().replace("_", "-")


def capability(key: str, meta: dict, categories: list[str]) -> str:
    text = " ".join(filter(None, [key, meta.get("name"), meta.get("fullName"), meta.get("format")]))
    for pattern, cap in CAPABILITY_BY_NAME:
        if pattern.search(text):
            return cap
    for cat in [meta.get("category"), *categories]:
        if cat in CAPABILITIES:
            return CAPABILITIES[cat]
    return "general"


def harness(meta: dict, key: str) -> str:
    if meta.get("authors") == "Artificial Analysis":
        return "artificial-analysis"
    if key.startswith("vals"):
        return "vals-ai"
    return "published"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="download the BenchLM files first")
    args = ap.parse_args()
    if args.fetch:
        fetch()

    models = json.loads((SNAPSHOT / "models.json").read_text())
    meta = {b["benchmarkKey"]: b for b in json.loads((SNAPSHOT / "benchmarks.json").read_text())["items"]}
    retrieved = models["generatedAt"][:10]

    # every measured score, once per (model, benchmark key)
    scores: dict[tuple[str, str], float] = {}
    categories: dict[str, dict[str, None]] = {}   # a key's BenchLM categories, in first-seen order
    names: dict[str, tuple[str, str]] = {}
    for m in models["items"]:
        for cat, results in (m.get("benchmarks") or {}).items():
            for key, value in results.items():
                if value is None:
                    continue
                scores.setdefault((m["slug"], key), float(value))
                categories.setdefault(key, {})[cat] = None
        names[m["slug"]] = (m["model"], m.get("releaseDate") or "")

    by_key: dict[str, list[float]] = {}
    for (_, key), v in scores.items():
        by_key.setdefault(key, []).append(v)

    versions = {}
    taken: set[str] = set()
    skipped = []
    for key, values in sorted(by_key.items()):
        b = meta.get(key, {})
        # the format's first clause: "Pass@1 with confidence interval, cost, ..." is a percentage
        what = " ".join(filter(None, [key, b.get("name"), (b.get("format") or "").split(",")[0]]))
        if min(values) < 0 or max(values) > 100 or NOT_PERCENT.search(what):
            skipped.append(key)
            continue
        url = b.get("url") or "https://benchlm.ai/benchmarks"
        # two keys can share a BenchLM page (AA's GPQA Diamond and the reported one)
        page = url.rstrip("/").rsplit("/", 1)[-1] if b.get("url") else None
        # aaGpqaDiamond -> aa-gpqa-diamond, so the reported results keep gpqa-diamond
        prefer = (kebab(key), page) if key.startswith("aa") else (page, kebab(key))
        name = next(n for n in (*prefer, key.lower()) if n and n not in taken)
        taken.add(name)
        versions[key] = {
            "benchmark": name,
            # BenchLM's names for Vals AI's runs of public benchmarks end in a word the harness already says
            "label": re.sub(r"\s+mirror$", "", b.get("name") or LABELS.get(key, key)),
            "capability": capability(key, b, list(categories[key])),
            "harness": harness(b, key),
            "source_url": url,
        }

    # two keys with one name (Terminal-Bench 2.1, published and run by Vals AI):
    # the harness tells them apart
    by_label: dict[str, list[str]] = {}
    for key, v in versions.items():
        by_label.setdefault(v["label"], []).append(key)
    for keys in by_label.values():
        if len(keys) < 2:
            continue
        for key in keys:
            v = versions[key]
            if v["harness"] in HARNESS_NAMES:
                v["label"] += f" ({HARNESS_NAMES[v['harness']]})"

    rows = []
    for (slug, key), value in sorted(scores.items()):
        if key not in versions:
            continue
        v = versions[key]
        name, release = names[slug]
        rows.append(
            {
                "model_slug": slug,
                "model_name": name,
                "release": release,
                "benchmark": v["benchmark"],
                "version": VERSION,
                "label": v["label"],
                "featured": "1" if key in FEATURED else "",
                "capability": v["capability"],
                "unit": "fraction",
                "harness": v["harness"],
                "score": f"{value / 100:.6f}",
                "source_url": v["source_url"],
                "retrieved_at": retrieved,
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n_models = len({r["model_slug"] for r in rows})
    print(f"wrote {OUT}: {len(rows)} scores, {n_models} models, {len(versions)} benchmarks")
    print(f"left out {len(skipped)} benchmarks that are not percentages: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
