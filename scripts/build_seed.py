"""Build the canonical long-format seed CSV from the raw Artificial Analysis extracts.

Reads data/raw/aa_tb2_entries.csv and data/raw/aa_tb4_entries.csv (columns:
slug,name,release,tb21,tb40) and writes data/seed/scores.csv in long format:

    model_slug,model_name,release,benchmark,version,harness,score,source_url,retrieved_at

Run from the repo root:  python scripts/build_seed.py
"""
from __future__ import annotations

import csv
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"
OUT = SEED_DIR / "scores.csv"

BENCH = "terminal-bench"
HARNESS = "artificial-analysis"
RETRIEVED = "2026-10-07"
URLS = {
    "2.1": "https://artificialanalysis.ai/evaluations/terminalbench-2-1",
    "4.0": "https://artificialanalysis.ai/evaluations/terminalbench-4-0",
}


def read_entries(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            rows[row["slug"]] = row
    return rows


def main() -> None:
    merged: dict[str, dict] = {}
    for fname in ("aa_tb2_entries.csv", "aa_tb4_entries.csv"):
        for slug, row in read_entries(RAW / fname).items():
            rec = merged.setdefault(
                slug,
                {"name": row["name"], "release": row["release"], "scores": {}},
            )
            for col, ver in (("tb21", "2.1"), ("tb40", "4.0")):
                if row.get(col):
                    rec["scores"].setdefault(ver, float(row[col]))

    SEED_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "model_slug",
                "model_name",
                "release",
                "benchmark",
                "version",
                "harness",
                "score",
                "source_url",
                "retrieved_at",
            ]
        )
        for slug in sorted(merged):
            rec = merged[slug]
            for ver in ("2.1", "4.0"):
                if ver in rec["scores"]:
                    w.writerow(
                        [
                            slug,
                            rec["name"],
                            rec["release"],
                            BENCH,
                            ver,
                            HARNESS,
                            f"{rec['scores'][ver]:.6f}",
                            URLS[ver],
                            RETRIEVED,
                        ]
                    )
    n_models = len(merged)
    n_scores = sum(len(r["scores"]) for r in merged.values())
    print(f"wrote {OUT}: {n_models} models, {n_scores} measured scores")


if __name__ == "__main__":
    main()
