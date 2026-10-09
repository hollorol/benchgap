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
from .families import load_families
from .harness_tax import MIN_OVERLAP
from .ingest import ingest_csv
from .report import (
    DEFAULT_MIN_BENCHMARKS,
    DEFAULT_MIN_MODELS,
    mapping_summary,
    multi_mapping_summary,
    render_matrix,
)
from .fitting import predict as predict_with

DEFAULT_DB = Path("data") / "benchgap.db"
DEFAULT_SEED = Path("data") / "seed" / "scores.csv"


def _conn(args: argparse.Namespace) -> sqlite3.Connection:
    path = Path(args.db)
    if not path.exists() and args.command != "init":
        sys.exit(f"error: database {path} does not exist; run 'benchgap init' first")
    return connect(path)


JOBS_HELP = "processes to fit on (default 0: every core; 1: no parallelism)"
CACHE_HELP = "directory to keep fit results in between runs; unchanged fits are reused"


def _print_kept(done: str, what: str, summary: list[dict], cache) -> None:
    kept = sum(1 for s in summary if not s.get("rejected"))
    print(f"{done}, kept {kept} {what}{cache.stats()}")


def cmd_init(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    init_db(conn)
    print(f"initialized {args.db}")


def cmd_ingest(args: argparse.Namespace) -> None:
    conn = _conn(args)
    n = ingest_csv(conn, args.csv)
    print(f"ingested {n} measured scores from {args.csv}")


def cmd_fit(args: argparse.Namespace) -> None:
    from .cache import FitCache

    conn = _conn(args)
    cache = FitCache(args.cache, "fit")
    summary = fit_mappings(conn, keep=args.keep, jobs=args.jobs, cache=cache)
    cache.save()
    if not summary:
        print("no version pairs with enough paired models to fit")
        return
    for s in summary:
        if s.get("rejected"):
            print(
                f"{s['from']} -> {s['to']}: n={s['n_pairs']}"
                f" rejected (quality gate: {s['rejected']})"
            )
            continue
        print(
            f"{s['from']} -> {s['to']} [{s['capability']}]: n={s['n_pairs']}"
            f" best={s['best_method']} (LOO RMSE {s['best_LOO_RMSE'] * 100:.2f} pp)"
        )
        if args.verbose:
            for method, m in sorted(s["candidates"].items(), key=lambda kv: -kv[1]["R2"]):
                print(
                    f"    {method:<10} R2={m['R2']:.3f}"
                    f" LOO RMSE={m['LOO_RMSE'] * 100:.2f} pp"
                )
    _print_kept(f"fitted {len(summary)} version pairs", "mappings", summary, cache)


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


def cmd_harness_tax(args: argparse.Namespace) -> None:
    from . import harness_tax

    conn = _conn(args)
    try:
        analysis = harness_tax.run(
            conn,
            families=load_families(Path(args.families)) if args.families else None,
            min_overlap=args.min_overlap,
            output=Path(args.output) if args.output else None,
            history=Path(args.history) if args.history else None,
        )
    except harness_tax.HarnessTaxError as e:
        sys.exit(f"error: {e}")
    candidates = [f for f in analysis["families"] if f.get("origin") == "auto"]
    for f in candidates:
        print(f"candidate family (auto-detected, review before promoting): {f['family_id']}"
              f" [{f['versions'][0]['name']} + {len(f['versions']) - 1} more]")
    for o in analysis["audit"]["outliers"]:
        print(f"audit: |{o['delta_pp']:.1f} pp| on {o['family_id']} (model {o['model_id']})")
    ratio = analysis["aggregates"]["headline"]["ratio"]
    print(
        f"{len(analysis['pairs'])} pairs over {len(analysis['families'])} families"
        f" ({sum(1 for p in analysis['pairs'] if p['low_overlap'])} low-overlap,"
        f" {len(analysis['audit']['outliers'])} outliers);"
        f" harness tax: agentic vs tool-free headline ratio"
        f" {f'{ratio:.1f}x' if ratio is not None else 'n/a'}"
    )
    if args.output:
        print(f"wrote {args.output}")
    if args.history:
        print(f"appended {args.history}")


def cmd_holdout(args: argparse.Namespace) -> None:
    from . import holdout

    conn = _conn(args)
    runs = holdout.store(conn, Path(args.file) if args.file else None)
    print(f"stored {len(runs)} holdout runs (replacing any earlier ones)")
    for run in runs:
        kind = f"sparse k={run['k']}" if run["scheme"] == "sparse" else "random mask"
        print(
            f"{run['run_id']} ({kind}): filled {run['n_filled']}/{run['n_masked']}"
            f" ({run['coverage'] * 100:.0f}%), MAE {run['pipeline']['mae_pp']:.1f} pp"
        )
    headline = holdout.summary(conn)["headline"]
    if headline:
        levels = headline["by_level_mae_pp"]
        print(
            f"headline: pipeline {headline['pipeline_vs_svd2_pct']:.0f}% lower MAE than a rank-2"
            f" completion; levels {levels['high']}/{levels['medium']}/{levels['low']} pp MAE"
            f" (high/medium/low); a published estimate carries about"
            f" {headline['reweighted']['mae_pp']:.1f} pp MAE ({headline['reweighted']['rmse_pp']:.1f} pp RMSE)"
        )


def cmd_multifit(args: argparse.Namespace) -> None:
    from .cache import FitCache
    from .fit import MAX_LOO_RMSE, MIN_PAIRS, MIN_R2
    from .multivariate import fit_multimappings

    conn = _conn(args)
    cache = FitCache(args.cache, "multifit")
    summary = fit_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE, jobs=args.jobs, cache=cache)
    cache.save()
    if not summary:
        print("no targets with enough same-capability overlap for multivariate fits")
        return
    for s in summary:
        if s.get("rejected"):
            print(f"{s['target']} <- {s['features']}: rejected ({s['rejected']})")
            continue
        print(
            f"{s['target']} <- {s['features']}: {s['method']}"
            f" (n={s['n']}, R2={s['R2']:.3f}, LOO RMSE {s['LOO_RMSE'] * 100:.2f} pp)"
        )
    _print_kept(f"searched {len(summary)} targets", "multivariate mappings", summary, cache)


def cmd_crossfit(args: argparse.Namespace) -> None:
    from .cache import FitCache
    from .fit import fit_cross_mappings

    conn = _conn(args)
    cache = FitCache(args.cache, "crossfit")
    summary = fit_cross_mappings(conn, jobs=args.jobs, cache=cache)
    cache.save()
    for s in summary if args.verbose else []:
        verdict = f"rejected (quality gate: {s['rejected']})" if s["rejected"] else f"LOO RMSE {s['best_LOO_RMSE'] * 100:.2f} pp"
        print(f"{s['from']} -> {s['to']}: n={s['n_pairs']} {verdict}")
    _print_kept(f"fitted {len(summary)} cross-capability pairs", "passing the quality gate", summary, cache)


def cmd_crossmultifit(args: argparse.Namespace) -> None:
    from .cache import FitCache
    from .fit import MAX_LOO_RMSE, MIN_PAIRS, MIN_R2
    from .multivariate import fit_cross_multimappings

    conn = _conn(args)
    cache = FitCache(args.cache, "crossmultifit")
    summary = fit_cross_multimappings(conn, MIN_PAIRS, MIN_R2, MAX_LOO_RMSE, jobs=args.jobs, cache=cache)
    cache.save()
    for s in summary if args.verbose else []:
        verdict = f"rejected (quality gate: {s['rejected']})" if s["rejected"] else f"LOO RMSE {s['LOO_RMSE'] * 100:.2f} pp"
        print(f"{s['target']} ~ {' + '.join(s['features'])}: n={s['n']} {verdict}")
    _print_kept(f"fitted {len(summary)} targets from several benchmarks", "passing the quality gate", summary, cache)


def cmd_report(args: argparse.Namespace) -> None:
    from .report import display_filter

    conn = _conn(args)
    vids, mids = display_filter(conn, args.min_models, args.min_benchmarks)
    n_all_versions = conn.execute("SELECT COUNT(*) FROM benchmark_versions").fetchone()[0]
    n_all_models = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    if len(vids) < n_all_versions or len(mids) < n_all_models:
        print(
            f"display filter: showing {len(vids)}/{n_all_versions} benchmark versions"
            f" and {len(mids)}/{n_all_models} models"
            f" (min {args.min_models} models per version,"
            f" min {args.min_benchmarks} benchmarks per model)"
        )
        print()
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
    multi = multi_mapping_summary(conn)
    if multi:
        print("multivariate mappings:")
        for s in multi:
            feats = ", ".join(s["features"])
            print(
                f"  {s['target']} <- [{feats}]: {s['method']}"
                f" (n={s['n_pairs']}, R2={s['R2']:.3f},"
                f" LOO RMSE={s['LOO_RMSE_pp']:.2f} pp)"
            )
        print()
    print(render_matrix(conn, vids, mids))


def cmd_html(args: argparse.Namespace) -> None:
    from .html_report import generate_html_report

    conn = _conn(args)
    out = generate_html_report(
        conn, args.output, min_models=args.min_models, min_benchmarks=args.min_benchmarks
    )
    print(f"wrote {out}")


def cmd_sql(args: argparse.Namespace) -> None:
    from .mysql import write_sql

    conn = _conn(args)
    counts = write_sql(conn, args.output)
    rows = ", ".join(f"{t} {n}" for t, n in counts.items())
    print(f"wrote {args.output}: {len(counts)} tables ({rows})")
    if not conn.execute("SELECT 1 FROM scores WHERE source = 'gapfilled' LIMIT 1").fetchone():
        print("warning: the database has no gapfilled scores; run 'benchgap gapfill' first")


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
    fit.add_argument("-j", "--jobs", type=int, default=0, help=JOBS_HELP)
    fit.add_argument("--cache", metavar="DIR", help=CACHE_HELP)
    fit.set_defaults(func=cmd_fit)

    sub.add_parser("gapfill", help="fill missing scores using fitted mappings").set_defaults(
        func=cmd_gapfill
    )

    htax = sub.add_parser(
        "harness-tax",
        help="measure how much harnesses disagree about the same models (never used for estimates)",
    )
    htax.add_argument(
        "--families", metavar="JSON",
        help="benchmark family registry to use instead of the packaged families.json",
    )
    htax.add_argument(
        "--min-overlap", type=int, default=MIN_OVERLAP,
        help=f"pairs with fewer shared models are stored but flagged low_overlap (default {MIN_OVERLAP})",
    )
    htax.add_argument(
        "--output", default="data/harness_tax.json", metavar="JSON",
        help="write the full analysis here",
    )
    htax.add_argument(
        "--history", default="data/harness_tax_history.jsonl", metavar="JSONL",
        help="append each run's per-pair stats here (never rewritten)",
    )
    htax.set_defaults(func=cmd_harness_tax)

    ho = sub.add_parser(
        "holdout",
        help="store the shipped masked-holdout evaluation's results (the paper's validation)",
    )
    ho.add_argument(
        "--file", metavar="JSON",
        help="results file to use instead of the packaged holdout_results.json",
    )
    ho.set_defaults(func=cmd_holdout)

    mfit = sub.add_parser(
        "multifit",
        help="fit multivariate mappings (several benchmarks -> one)"
        " per target; gapfill prefers them when available",
    )
    mfit.add_argument("-j", "--jobs", type=int, default=0, help=JOBS_HELP)
    mfit.add_argument("--cache", metavar="DIR", help=CACHE_HELP)
    mfit.set_defaults(func=cmd_multifit)

    cfit = sub.add_parser(
        "crossfit",
        help="fit pairs across capabilities, for the cross-domain view (never used for estimates)",
    )
    cfit.add_argument("-v", "--verbose", action="store_true", help="show every pair")
    cfit.add_argument("-j", "--jobs", type=int, default=0, help=JOBS_HELP)
    cfit.add_argument("--cache", metavar="DIR", help=CACHE_HELP)
    cfit.set_defaults(func=cmd_crossfit)

    cmfit = sub.add_parser(
        "crossmultifit",
        help="fit each benchmark from several of any capability, for the multivariate view"
        " (never used for estimates; run after fit and crossfit)",
    )
    cmfit.add_argument("-v", "--verbose", action="store_true", help="show every target")
    cmfit.add_argument("-j", "--jobs", type=int, default=0, help=JOBS_HELP)
    cmfit.add_argument("--cache", metavar="DIR", help=CACHE_HELP)
    cmfit.set_defaults(func=cmd_crossmultifit)

    rep = sub.add_parser("report", help="show mappings and the score matrix")
    rep.add_argument("--min-models", type=int, default=DEFAULT_MIN_MODELS,
                    help="display only benchmark versions with at least this many"
                    " measured models (0 = show all)")
    rep.add_argument("--min-benchmarks", type=int, default=DEFAULT_MIN_BENCHMARKS,
                    help="display only models measured on at least this many"
                    " benchmarks (0 = show all)")
    rep.set_defaults(func=cmd_report)

    html = sub.add_parser("html", help="write a self-contained HTML report")
    html.add_argument(
        "output", default="data/report.html", nargs="?", help="output HTML path"
    )
    html.add_argument("--min-models", type=int, default=DEFAULT_MIN_MODELS,
                     help="display only benchmark versions with at least this many"
                     " measured models (0 = show all)")
    html.add_argument("--min-benchmarks", type=int, default=DEFAULT_MIN_BENCHMARKS,
                     help="display only models measured on at least this many"
                     " benchmarks (0 = show all)")
    html.set_defaults(func=cmd_html)

    sql = sub.add_parser(
        "sql", help="write the database as a MySQL load script (for benchgap.net)"
    )
    sql.add_argument("output", default="dist/benchgap.sql", nargs="?", help="output .sql path")
    sql.set_defaults(func=cmd_sql)

    pred = sub.add_parser(
        "predict", help="map a score between two versions (fractions or percent)"
    )
    pred.add_argument("from_version", help="e.g. terminal-bench-4/current")
    pred.add_argument("to_version", help="e.g. aa-terminal-bench4/current")
    pred.add_argument("value", type=float, help="score on the source version")
    pred.set_defaults(func=cmd_predict)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
