"""benchgap command-line interface."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .db import connect, init_db, parse_version_spec
from .fit import fit_mappings
from .gapfill import gapfill
from .ingest import ingest_csv
from .report import mapping_summary, render_matrix
from .fitting import predict as predict_with

DEFAULT_DB = Path("data") / "benchgap.db"
DEFAULT_SEED = Path("data") / "seed" / "scores.csv"


def _conn(args: argparse.Namespace) -> sqlite3.Connection:
    path = Path(args.db)
    if not path.exists() and args.command != "init":
        sys.exit(f"error: database {path} does not exist; run 'benchgap init' first")
    return connect(path)


def cmd_init(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    init_db(conn)
    print(f"initialized {args.db}")


def cmd_ingest(args: argparse.Namespace) -> None:
    conn = _conn(args)
    n = ingest_csv(conn, args.csv)
    print(f"ingested {n} measured scores from {args.csv}")


def cmd_fit(args: argparse.Namespace) -> None:
    conn = _conn(args)
    summary = fit_mappings(conn, keep=args.keep)
    if not summary:
        print("no version pairs with enough paired models to fit")
        return
    for s in summary:
        print(
            f"{s['from']} -> {s['to']}: n={s['n_pairs']}"
            f" best={s['best_method']} (LOO RMSE {s['best_LOO_RMSE'] * 100:.2f} pp)"
        )
        if args.verbose:
            for method, m in sorted(s["candidates"].items(), key=lambda kv: -kv[1]["R2"]):
                print(
                    f"    {method:<10} R2={m['R2']:.3f}"
                    f" LOO RMSE={m['LOO_RMSE'] * 100:.2f} pp"
                )


def cmd_gapfill(args: argparse.Namespace) -> None:
    conn = _conn(args)
    filled = gapfill(conn)
    for f in filled:
        mark = " (extrapolated)" if f["extrapolated"] else ""
        print(
            f"{f['slug']}: {f['target']} = {f['value'] * 100:.1f}%"
            f" via {f['method']}{mark}"
        )
    print(f"gapfilled {len(filled)} missing scores")


def cmd_report(args: argparse.Namespace) -> None:
    conn = _conn(args)
    summary = mapping_summary(conn)
    if summary:
        print("mappings:")
        for s in summary:
            print(
                f"  {s['from']} -> {s['to']}: {s['method']}"
                f" (n={s['n_pairs']}, R2={s['R2']:.3f},"
                f" LOO RMSE={s['LOO_RMSE_pp']:.2f} pp,"
                f" {s['candidates']} candidate fits)"
            )
        print()
    print(render_matrix(conn))


def cmd_html(args: argparse.Namespace) -> None:
    from .html_report import generate_html_report

    conn = _conn(args)
    out = generate_html_report(conn, args.output)
    print(f"wrote {out}")


def cmd_predict(args: argparse.Namespace) -> None:
    conn = _conn(args)
    src = parse_version_spec(conn, args.from_version)
    dst = parse_version_spec(conn, args.to_version)
    row = conn.execute(
        "SELECT m.method, m.params_json, m.metrics_json, m.train_range_json"
        " FROM mappings m"
        " WHERE m.from_version_id = ? AND m.to_version_id = ?"
        " ORDER BY COALESCE(json_extract(m.metrics_json, '$.LOO_RMSE'), 1e9),"
        "          COALESCE(json_extract(m.metrics_json, '$.RMSE'), 1e9)"
        " LIMIT 1",
        (src["id"], dst["id"]),
    ).fetchone()
    if row is None:
        sys.exit(f"error: no fitted mapping {args.from_version} -> {args.to_version}")
    params = json.loads(row["params_json"])
    # Accept percent (e.g. 59.6) or fraction (e.g. 0.596); scores are stored
    # as fractions.
    x = args.value / 100.0 if args.value > 1.5 else args.value
    y = float(predict_with(row["method"], params, [x])[0])
    train_range = json.loads(row["train_range_json"]) if row["train_range_json"] else None
    note = ""
    if train_range and not (train_range["x_min"] <= x <= train_range["x_max"]):
        note = "  [outside training range - extrapolated]"
    print(f"{x * 100:.1f}% on {args.from_version} -> {y * 100:.1f}% on {args.to_version}"
          f" ({row['method']}){note}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="benchgap",
        description="Benchmark score database with gapfilling of missing scores",
    )
    p.add_argument("--version", action="version", version=f"benchgap {__version__}")
    p.add_argument("--db", default=str(DEFAULT_DB), help="path to the SQLite database")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database schema").set_defaults(func=cmd_init)

    ing = sub.add_parser("ingest", help="load measured scores from a seed CSV")
    ing.add_argument("csv", default=str(DEFAULT_SEED), nargs="?", help="seed CSV path")
    ing.set_defaults(func=cmd_ingest)

    fit = sub.add_parser("fit", help="fit cross-version mappings from paired scores")
    fit.add_argument(
        "--keep",
        choices=["best", "all"],
        default="best",
        help="store only the best mapping per pair (default) or all candidates",
    )
    fit.add_argument("-v", "--verbose", action="store_true", help="show all candidates")
    fit.set_defaults(func=cmd_fit)

    sub.add_parser("gapfill", help="fill missing scores using fitted mappings").set_defaults(
        func=cmd_gapfill
    )

    sub.add_parser("report", help="show mappings and the score matrix").set_defaults(
        func=cmd_report
    )

    html = sub.add_parser("html", help="write a self-contained HTML report")
    html.add_argument(
        "output", default="data/report.html", nargs="?", help="output HTML path"
    )
    html.set_defaults(func=cmd_html)

    pred = sub.add_parser(
        "predict", help="map a score between two versions (fractions or percent)"
    )
    pred.add_argument("from_version", help="e.g. terminal-bench/4.0")
    pred.add_argument("to_version", help="e.g. terminal-bench/2.1")
    pred.add_argument("value", type=float, help="score on the source version")
    pred.set_defaults(func=cmd_predict)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
