"""Regenerate the paper-ready exports (exports/paper/) from the latest harness-tax
analysis (data/harness_tax.json, written by `benchgap harness-tax`):

  table1_pairs.csv               every pair with its metrics (Table 1)
  figure1_harness_tax_by_pair.json  chart-ready rows, ascending by mean |delta| (Figure 1)
  figure2_hle_tools_effect.json  per-model HLE tools on/off deltas (Figure 2)
  aggregates.json                pooled tier stats and the headline ratio

Run after every harness-tax run: uv run python scripts/export_paper.py
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ANALYSIS = Path("data") / "harness_tax.json"
EXPORTS = Path("exports") / "paper"

# the pair whose per-model tools effect is Figure 2 (families.json: hle-protocol-variants)
FIGURE2_FAMILY = "hle-protocol-variants"

COLUMNS = [
    "pair", "family", "capability", "tier", "n", "mean_abs_pp", "median_abs_pp",
    "max_abs_pp", "share_gt_10pp", "kendall_tau", "sign_test_p", "directionality",
    "same_item_set",
]


def export(analysis: dict, out_dir: Path) -> list[Path]:
    pairs = analysis["pairs"]
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    table1 = out_dir / "table1_pairs.csv"
    with table1.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for p in sorted(pairs, key=lambda p: -(p["metrics"]["mean_abs_pp"] or 0)):
            m = p["metrics"]
            w.writerow([
                f"{p['a']['name']} vs {p['b']['name']}", p["family_id"], p["capability"],
                p["tier"] or "", m["n"], m["mean_abs_pp"], m["median_abs_pp"], m["max_abs_pp"],
                m["share_gt_10"], m["kendall_tau"], m["sign_p"], m["directionality"],
                p["same_item_set"],
            ])
    written.append(table1)

    figure1 = out_dir / "figure1_harness_tax_by_pair.json"
    rows = [
        {
            "pair": f"{p['a']['name']} vs {p['b']['name']}",
            "family": p["family_id"], "tier": p["tier"], "n": p["metrics"]["n"],
            "mean_abs_pp": p["metrics"]["mean_abs_pp"], "kendall_tau": p["metrics"]["kendall_tau"],
            "same_item_set": p["same_item_set"],
        }
        for p in pairs if p["metrics"]["n"] > 0
    ]
    rows.sort(key=lambda r: r["mean_abs_pp"])
    figure1.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    written.append(figure1)

    figure2 = out_dir / "figure2_hle_tools_effect.json"
    fig2 = [
        {"model": d["model_id"], "score_a_pp": d["score_a"] * 100, "score_b_pp": d["score_b"] * 100,
         "delta_pp": d["delta_pp"]}
        for p in pairs if p["family_id"] == FIGURE2_FAMILY
        for d in p["deltas"]
    ]
    figure2.write_text(
        json.dumps({
            "family": FIGURE2_FAMILY,
            "note": "delta_pp is the baseline (tools off) minus the with-tools score, per model",
            "models": sorted(fig2, key=lambda m: m["delta_pp"]),
        }, indent=2) + "\n", encoding="utf-8")
    written.append(figure2)

    aggregates = out_dir / "aggregates.json"
    aggregates.write_text(
        json.dumps({
            "generated_at": analysis["generated_at"],
            "data_retrieved_at": analysis["data_retrieved_at"],
            "measured_only": True,
            "aggregates": analysis["aggregates"],
        }, indent=2) + "\n", encoding="utf-8")
    written.append(aggregates)
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--analysis", default=str(ANALYSIS), help="the harness_tax.json to read")
    parser.add_argument("--out", default=str(EXPORTS), help="the directory to write")
    args = parser.parse_args(argv)
    analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    for path in export(analysis, Path(args.out)):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
