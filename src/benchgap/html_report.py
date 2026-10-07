"""Self-contained HTML report generation.

Renders the database state (mappings, fitted curves, gapfilled score matrix)
into a single static HTML file. Charts are rendered with matplotlib and
embedded as base64 PNG data URIs, so the file is fully self-contained and
works offline.
"""
from __future__ import annotations

import base64
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .fitting import CANDIDATES, fit_all, predict, select_best  # noqa: E402
from .report import (  # noqa: E402
    CAPABILITY_ORDER,
    DEFAULT_MIN_BENCHMARKS,
    DEFAULT_MIN_MODELS,
    display_filter,
    mapping_summary,
    multi_mapping_summary,
    predictability_matrix,
    score_matrix,
)

COLORS = {
    "measured": "#2563eb",
    "gapfilled": "#d97706",
    "best": "#111827",
    "candidates": ["#9ca3af", "#6b7280", "#f59e0b", "#10b981"],
    "range": "#f3f4f6",
}

# Maximum number of mapping figure cards rendered in the report.
MAX_MAPPING_CARDS = 12


def _labels(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        r["id"]: f"{r['benchmark']}/{r['version']}"
        + (f"@{r['harness']}" if r["harness"] != "unknown" else "")
        for r in conn.execute(
            "SELECT v.id, v.version, v.harness, b.name AS benchmark"
            " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        )
    }


def _used_mappings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Best mappings that produced at least one gapfilled score, by usage."""
    return conn.execute(
        "SELECT m.*, (SELECT COUNT(*) FROM scores s"
        "   WHERE s.mapping_id = m.id AND s.source = 'gapfilled') AS n_used"
        " FROM mappings m"
        " WHERE m.id = ("
        "   SELECT m2.id FROM mappings m2"
        "    WHERE m2.from_version_id = m.from_version_id"
        "      AND m2.to_version_id = m.to_version_id"
        "    ORDER BY COALESCE(json_extract(m2.metrics_json, '$.LOO_RMSE'), 1e9),"
        "             COALESCE(json_extract(m2.metrics_json, '$.RMSE'), 1e9)"
        "    LIMIT 1)"
        "   AND EXISTS (SELECT 1 FROM scores s2 WHERE s2.mapping_id = m.id"
        "               AND s2.source = 'gapfilled')"
        " ORDER BY n_used DESC, m.from_version_id LIMIT ?",
        (MAX_MAPPING_CARDS,),
    ).fetchall()


def _fig_to_data_uri(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _mapping_figure(
    conn: sqlite3.Connection,
    mapping: sqlite3.Row,
    label_from: str,
    label_to: str,
    gapfilled: list[dict],
) -> str:
    """Scatter of paired scores with candidate curves, plus gapfilled points."""
    points = conn.execute(
        "SELECT p.x, p.y, m.slug FROM mapping_points p"
        " JOIN models m ON m.id = p.model_id"
        " WHERE p.mapping_id = ?",
        (mapping["id"],),
    ).fetchall()
    xs = np.array([p["x"] for p in points])
    ys = np.array([p["y"] for p in points])

    results = fit_all(xs, ys)
    best = select_best(results)  # never None: the stored mapping's own method converged on these points
    params = json.loads(mapping["params_json"])
    train_range = json.loads(mapping["train_range_json"])

    fig, ax = plt.subplots(figsize=(6.2, 4.2))

    x_min, x_max = 0.0, max(1.05 * xs.max(), 1e-3)
    grid = np.linspace(0, x_max, 400)

    # training-range band and candidate curves
    if train_range:
        ax.axvspan(train_range["x_min"], train_range["x_max"], color=COLORS["range"], zorder=0)
    for i, r in enumerate(sorted(results, key=lambda r: -r.metrics["LOO_RMSE"])):
        if r.method == best.method:
            continue
        curve = predict(r.method, r.params, grid)
        ax.plot(grid * 100, curve * 100, color=COLORS["candidates"][i % 4], lw=1.2, alpha=0.8,
                label=f"{r.method} (LOO {r.metrics['LOO_RMSE'] * 100:.1f} pp)")
    curve = predict(best.method, params, grid)
    ax.plot(grid * 100, curve * 100, color=COLORS["best"], lw=2.0,
            label=f"{best.method} (LOO {best.metrics['LOO_RMSE'] * 100:.1f} pp)")

    ax.scatter(xs * 100, ys * 100, s=30, color=COLORS["measured"], zorder=3,
               label=f"paired models (n={len(xs)})")
    if gapfilled:
        gx = np.array([g["input"] for g in gapfilled])
        gy = np.array([g["value"] for g in gapfilled])
        ax.scatter(gx * 100, gy * 100, s=32, facecolors="none", edgecolors=COLORS["gapfilled"],
                   lw=1.4, zorder=3, label=f"gapfilled (n={len(gx)})")
    ax.set_xlabel(f"{label_from} score (%)")
    ax.set_ylabel(f"{label_to} score (%)")
    ax.set_title(f"{label_from} → {label_to}", fontsize=10)
    ax.legend(fontsize=7, loc="lower right", framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def _mapping_card(conn: sqlite3.Connection, mapping: sqlite3.Row, labels: dict[int, str]) -> str:
    params = json.loads(mapping["params_json"])
    metrics = json.loads(mapping["metrics_json"])
    eq = CANDIDATES[mapping["method"]].equation
    label_from = labels[mapping["from_version_id"]]
    label_to = labels[mapping["to_version_id"]]

    gapfilled = [
        {
            "input": json.loads(r["prediction_json"])["input_score"],
            "value": r["value"],
        }
        for r in conn.execute(
            "SELECT value, prediction_json FROM scores"
            " WHERE mapping_id = ? AND source = 'gapfilled'",
            (mapping["id"],),
        )
    ]

    fig_uri = _mapping_figure(conn, mapping, label_from, label_to, gapfilled)
    metrics_rows = (
        f"<tr><td>n (paired models)</td><td>{mapping['n_points']}</td></tr>"
        f"<tr><td>R²</td><td>{metrics['R2']:.4f}</td></tr>"
        f"<tr><td>LOO-CV RMSE</td><td><b>{metrics['LOO_RMSE'] * 100:.2f} pp</b></td></tr>"
        f"<tr><td>gapfilled scores</td><td>{mapping['n_used']}</td></tr>"
    )
    return f"""
    <section class="card">
      <h2>{label_from} → {label_to} <span class="badge">{mapping['n_used']} gapfilled</span></h2>
      <p class="equation">selected method: <b>{mapping['method']}</b> &nbsp;|&nbsp;
         <code>{eq.format(**params)}</code> &nbsp;(scores as fractions)</p>
      <img src="{fig_uri}" alt="fit for {label_from} to {label_to}">
      <table class="metrics">{metrics_rows}</table>
    </section>"""


def _matrix_html(
    conn: sqlite3.Connection,
    include_versions: set[int] | None = None,
    include_models: set[int] | None = None,
) -> str:
    columns, rows = score_matrix(conn, include_versions, include_models)
    # tooltip metadata per (model, version): which method gapfilled the cell
    meta = {
        (r["model_id"], r["version_id"]): r
        for r in conn.execute(
            "SELECT model_id, version_id, prediction_json FROM scores"
            " WHERE source = 'gapfilled'"
        )
    }
    tooltips = {}
    for key, r in meta.items():
        p = json.loads(r["prediction_json"])
        kind = "multivariate" if p.get("kind") == "multi" else "univariate"
        n_inputs = len(p.get("input_scores", {})) or 1
        tooltips[key] = (
            f"gapfilled via {kind} {p['method']} from {n_inputs} source benchmark(s),"
            " not a measured score"
        )
    order = sorted(
        range(len(rows)),
        key=lambda i: max((c["value"] or -1) for c in rows[i]["cells"]),
        reverse=True,
    )
    # capability header row with colspans
    spans = []
    for col in columns:
        if spans and spans[-1][0] == col["capability"]:
            spans[-1][1] += 1
        else:
            spans.append([col["capability"], 1])
    cap_row = "".join(
        f'<th class="cap" colspan="{n}">{cap}</th>' for cap, n in spans
    )
    head = "".join(f"<th>{c['label']}</th>" for c in columns)
    body = []
    for i in order:
        r = rows[i]
        cells = []
        for col, c in zip(columns, r["cells"]):
            if c["value"] is None:
                cells.append('<td class="missing">–</td>')
            elif c["kind"] == "g":
                tip = tooltips.get((r["id"], col["id"]), "gapfilled: fitted mapping")
                cells.append(
                    f'<td class="gapfilled" title="{tip}">{c["value"] * 100:.1f}</td>'
                )
            else:
                cells.append(f"<td>{c['value'] * 100:.1f}</td>")
        body.append(f"<tr><th class='rowhead'>{r['slug']}</th>{''.join(cells)}</tr>")
    return (
        "<table class='matrix'>"
        f"<tr><th class='rowhead cap'>capability</th>{cap_row}</tr>"
        f"<tr><th class='rowhead'>model</th>{head}</tr>{''.join(body)}</table>"
    )


def _predictability_color(loo_pp: float) -> str:
    """Green -> amber -> red across the 0-15 pp LOO range (the quality gate)."""
    t = max(0.0, min(loo_pp / 15.0, 1.0))
    stops = [(0.0, (16, 185, 129)), (0.5, (245, 158, 11)), (1.0, (239, 68, 68))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            rgb = tuple(int(a + (b - a) * u) for a, b in zip(c0, c1))
            return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"
    return "rgb(239,68,68)"


def _predictability_html(
    conn: sqlite3.Connection, include_versions: set[int] | None = None
) -> str:
    """Color-coded source->target predictability matrix (LOO RMSE per pair)."""
    versions, cells = predictability_matrix(conn, include_versions)
    if not versions or not any(any(r) for r in cells):
        return ""
    head = "".join(
        f'<th class="pm {"brk" if j > 0 and versions[j]["capability"] != versions[j-1]["capability"] else ""}">'
        f"{v['label']}</th>"
        for j, v in enumerate(versions)
    )
    body = []
    for i, src in enumerate(versions):
        row_cells = []
        for j, dst in enumerate(versions):
            brk = ' brk' if j > 0 and versions[j]["capability"] != versions[j - 1]["capability"] else ""
            if i == j:
                row_cells.append(f'<td class="pm diag{brk}"></td>')
            elif src["capability"] != dst["capability"]:
                row_cells.append(
                    f'<td class="pm na{brk}" title="never fitted: different capability'
                    f' ({src["capability"]} vs {dst["capability"]})"></td>'
                )
            else:
                m = cells[i][j]
                if m is None:
                    row_cells.append(
                        f'<td class="pm none{brk}" title="no mapping: not enough paired'
                        ' models or the best fit failed the quality gate"></td>'
                    )
                else:
                    color = _predictability_color(m["LOO_RMSE"] * 100)
                    tip = (
                        f"{m['method']}: n={m['n_pairs']}, R2={m['R2']:.3f},"
                        f" LOO RMSE={m['LOO_RMSE'] * 100:.2f} pp"
                    )
                    row_cells.append(
                        f'<td class="pm{brk}" style="background:{color}"'
                        f' title="{tip}">{m["LOO_RMSE"] * 100:.1f}</td>'
                    )
        row_brk = ' brk' if i > 0 and src["capability"] != versions[i - 1]["capability"] else ""
        body.append(
            f'<tr><th class="rowhead{row_brk}">{src["label"]}</th>{"".join(row_cells)}</tr>'
        )
    return (
        '<table class="pmatrix">'
        f'<tr><th class="rowhead">source ↓ / target →</th>{head}</tr>'
        f'{"".join(body)}</table>'
    )


def _capability_stats(conn: sqlite3.Connection) -> list[tuple[str, int, int, int]]:
    """(capability, n_benchmarks, n_measured, n_gapfilled) per capability."""
    return [
        (r["capability"], r["n_versions"], r["n_measured"], r["n_gapfilled"])
        for r in conn.execute(
            "SELECT b.capability AS capability,"
            " COUNT(DISTINCT v.id) AS n_versions,"
            " SUM(CASE WHEN s.source = 'measured' THEN 1 ELSE 0 END) AS n_measured,"
            " SUM(CASE WHEN s.source = 'gapfilled' THEN 1 ELSE 0 END) AS n_gapfilled"
            " FROM benchmarks b"
            " JOIN benchmark_versions v ON v.benchmark_id = b.id"
            " LEFT JOIN scores s ON s.version_id = v.id"
            " GROUP BY b.capability"
            " ORDER BY b.capability"
        )
    ]


def generate_html_report(
    conn: sqlite3.Connection,
    path: str | Path,
    min_models: int = DEFAULT_MIN_MODELS,
    min_benchmarks: int = DEFAULT_MIN_BENCHMARKS,
) -> Path:
    """Write a self-contained HTML report; returns the output path.

    ``min_models`` / ``min_benchmarks`` apply the dense-core display filter
    (iteratively dropped sparse benchmarks and thin models) to the score
    and predictability matrices only; pass 0 to show everything. The
    database, mappings, and all other sections are unaffected.
    """
    labels = _labels(conn)
    vids, mids = display_filter(conn, min_models, min_benchmarks)
    n_all_versions = conn.execute("SELECT COUNT(*) FROM benchmark_versions").fetchone()[0]
    n_all_models = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    stats = {
        "models": conn.execute("SELECT COUNT(*) FROM models").fetchone()[0],
        "versions": conn.execute("SELECT COUNT(*) FROM benchmark_versions").fetchone()[0],
        "measured": conn.execute(
            "SELECT COUNT(*) FROM scores WHERE source = 'measured'"
        ).fetchone()[0],
        "gapfilled": conn.execute(
            "SELECT COUNT(*) FROM scores WHERE source = 'gapfilled'"
        ).fetchone()[0],
    }
    summary = mapping_summary(conn)
    multi = multi_mapping_summary(conn)
    mappings = _used_mappings(conn)
    if not mappings:
        # nothing gapfilled yet: show the single best-fitting mapping instead
        row = conn.execute(
            "SELECT m.* FROM mappings m ORDER BY"
            " COALESCE(json_extract(m.metrics_json, '$.LOO_RMSE'), 1e9) LIMIT 1"
        ).fetchone()
        mappings = [row] if row is not None else []
    cards = "".join(_mapping_card(conn, m, labels) for m in mappings)
    summary_rows = "".join(
        f"<tr><td>{s['from']}</td><td>{s['to']}</td><td><b>{s['method']}</b></td>"
        f"<td>{s['n_pairs']}</td><td>{s['R2']:.3f}</td>"
        f"<td>{s['LOO_RMSE_pp']:.2f}</td></tr>"
        for s in summary
    )
    multi_rows = "".join(
        f"<tr><td>{s['target']}</td><td>{', '.join(s['features'])}</td>"
        f"<td><b>{s['method']}</b></td><td>{s['n_pairs']}</td>"
        f"<td>{s['R2']:.3f}</td><td>{s['LOO_RMSE_pp']:.2f}</td></tr>"
        for s in multi
    )
    multi_section = ""
    if multi:
        multi_section = f"""
<h2>Multivariate mappings (several benchmarks → one)</h2>
<table>
<thead><tr><th>target</th><th>source benchmarks</th><th>method</th><th>n</th>
<th>R²</th><th>LOO RMSE (pp)</th></tr></thead>
<tbody>{multi_rows}</tbody>
</table>
<p class="legend">Per target, up to three same-capability source benchmarks are
selected greedily by leave-one-out CV. Gapfill prefers a multivariate mapping
when the model is measured on every source benchmark it uses and its LOO RMSE
beats the best univariate mapping; otherwise the univariate mapping applies.
Models missing one of the source scores fall back to the univariate path.</p>
"""
    predictability_section = ""
    predictability_table = _predictability_html(conn, vids)
    predictability_note = ""
    if len(vids) < n_all_versions:
        predictability_note = (
            f" Shown for the dense-core benchmark set ({len(vids)} of"
            f" {n_all_versions} versions; see the score matrix note)."
        )
    if predictability_table:
        predictability_section = f"""
<h2>Predictability matrix</h2>
<div class="matrix-wrap">
{predictability_table}
</div>
<p class="legend">Rows are source benchmarks, columns are targets; each cell is
the leave-one-out CV RMSE (pp) of the best fitted mapping predicting the
column benchmark from the row benchmark - lower is better. Hover a cell for
the method, sample size, and R². Green → red spans 0 → 15 pp (the quality
gate); gray cells have no usable mapping (too little overlap, or the best
fit failed the gate); dark cells mark the diagonal; blank cells never have a
mapping because the pair crosses a capability boundary. Groups of
same-capability benchmarks are separated by heavier borders.{predictability_note}</p>
"""
    cap_rows = "".join(
        f"<tr><td>{cap}</td><td>{nb}</td><td>{meas}</td><td>{gap}</td></tr>"
        for cap, nb, meas, gap in _capability_stats(conn)
    )
    filter_note = ""
    if len(vids) < n_all_versions or len(mids) < n_all_models:
        filter_note = (
            f'<p class="legend">Dense-core view: showing {len(vids)} of'
            f" {n_all_versions} benchmark versions (each with at least"
            f" {min_models} measured models) and {len(mids)} of {n_all_models}"
            f" models (each measured on at least {min_benchmarks} benchmarks),"
            " peeled iteratively until both hold. The full dataset, all"
            " mappings, and all gapfilled scores are unaffected - this is a"
            " display filter only.</p>"
        )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>benchgap report</title>
<style>
  :root {{ --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f9fafb; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--ink);
         max-width: 1280px; margin: 2rem auto; padding: 0 1.5rem; background: #fff; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .sub {{ color: var(--muted); margin-top: 0; }}
  .stats {{ display: flex; gap: 1.5rem; margin: 1.2rem 0; flex-wrap: wrap; }}
  .stats div {{ background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
               padding: .6rem 1.1rem; min-width: 110px; }}
  .stats .n {{ font-size: 1.4rem; font-weight: 600; }}
  .stats .k {{ color: var(--muted); font-size: .8rem; }}
  table {{ border-collapse: collapse; margin: 0.5rem 0 1.5rem; }}
  th, td {{ padding: .35rem .8rem; text-align: left; border-bottom: 1px solid var(--line); }}
  thead th {{ border-bottom: 2px solid var(--ink); font-size: .85rem; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 1.2rem; }}
  .card {{ border: 1px solid var(--line); border-radius: 10px; padding: 1rem 1.2rem;
          margin: 0 0 1.2rem; flex: 1 1 460px; max-width: 620px; }}
  .card h2 {{ margin-top: 0; font-size: .95rem; }}
  .card img {{ width: 100%; height: auto; }}
  .badge {{ background: #fffbeb; color: #b45309; border-radius: 6px;
           font-size: .7rem; padding: .1rem .4rem; vertical-align: middle; }}
  .equation {{ color: var(--muted); font-size: .8rem; }}
  code {{ background: var(--bg); padding: .1rem .35rem; border-radius: 4px;
         font-size: .75rem; }}
  .metrics td:first-child {{ color: var(--muted); }}
  .metrics td, .metrics th {{ padding: .2rem .6rem; }}
  .matrix td, .matrix th {{ text-align: right; padding: .25rem .45rem; font-size: .78rem;
                           white-space: nowrap; }}
  .matrix .rowhead {{ text-align: left; font-weight: 500; position: sticky; left: 0;
                     background: #fff; }}
  .matrix th.cap {{ background: var(--bg); font-size: .7rem; color: var(--muted);
                   text-align: center; }}
  .matrix-wrap {{ overflow-x: auto; }}
  .matrix td.gapfilled {{ color: #b45309; background: #fffbeb; }}
  .matrix td.missing {{ color: #d1d5db; }}
  .pmatrix {{ border-collapse: collapse; font-size: .72rem; }}
  .pmatrix th, .pmatrix td {{ padding: .18rem .3rem; text-align: center;
                             border: 1px solid #f0f0f0; white-space: nowrap; }}
  .pmatrix th.rowhead {{ text-align: left; font-weight: 500; background: #fff;
                        position: sticky; left: 0; }}
  .pmatrix .brk {{ border-left-width: 3px; }}
  .pmatrix tr td.brk {{ border-left: 3px solid #9ca3af; }}
  .pmatrix td.diag {{ background: #111827; }}
  .pmatrix td.na {{ background: #f9fafb; border: 1px dotted #e5e7eb; }}
  .pmatrix td.none {{ background: #e5e7eb; }}
  .pmatrix td.pm {{ color: #111827; font-weight: 600; }}
  .pmatrix th.brk {{ border-left: 3px solid #9ca3af; }}
  .legend {{ color: var(--muted); font-size: .85rem; }}
  footer {{ color: var(--muted); font-size: .8rem; border-top: 1px solid var(--line);
           margin-top: 2rem; padding-top: .8rem; }}
</style>
</head>
<body>
<h1>benchgap report</h1>
<p class="sub">Benchmark score database with gapfilled values · generated {generated}</p>

<div class="stats">
  <div><div class="n">{stats['models']}</div><div class="k">models</div></div>
  <div><div class="n">{stats['versions']}</div><div class="k">benchmark versions</div></div>
  <div><div class="n">{stats['measured']}</div><div class="k">measured scores</div></div>
  <div><div class="n">{stats['gapfilled']}</div><div class="k">gapfilled scores</div></div>
</div>

<h2>Capabilities</h2>
<table>
<thead><tr><th>capability</th><th>benchmark versions</th><th>measured</th>
<th>gapfilled</th></tr></thead>
<tbody>{cap_rows}</tbody>
</table>
<p class="legend">Mappings are only fitted between benchmark versions of the same
capability. Cells for capabilities a model was never measured on (and has no
same-capability source score) stay empty - the gap is kept, not invented.</p>

<h2>Fitted mappings</h2>
<div class="matrix-wrap">
<table>
<thead><tr><th>from</th><th>to</th><th>selected method</th><th>n pairs</th>
<th>R²</th><th>LOO RMSE (pp)</th></tr></thead>
<tbody>{summary_rows}</tbody>
</table>
</div>
<p class="legend">Selection is by leave-one-out cross-validated RMSE among the fitted
candidates (linear, Michaelis–Menten, Michaelis–Menten with offset, the inverse
Michaelis–Menten form, Hill, and an offset logistic). All candidates are
monotone. Solid curves are the selected fit; gray curves are the alternatives.</p>

{multi_section}

{predictability_section}

<h2>Mapping detail (top {len(mappings)} by gapfill usage)</h2>
<div class="cards">{cards}</div>

<h2>Score matrix (percent; amber = gapfilled, dash = kept gap)</h2>
{filter_note}
<div class="matrix-wrap">
{_matrix_html(conn, vids, mids)}
</div>
<p class="legend">Amber cells are <b>gapfilled</b>: values predicted by the fitted
mapping, not measured scores. Dashes are kept gaps: no measured source score in
the same capability to predict from. Models are ordered by their best score.</p>

<footer>
Scores from Artificial Analysis evaluation leaderboards (harness:
artificial-analysis), retrieved 2026-10-07. Coefficients are harness-specific.
Gapfilled values outside a mapping's training range are flagged as extrapolated
in the database. Probabilistic fits (credible intervals) are planned; the
schema already carries ci95 columns.
</footer>
</body>
</html>"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
