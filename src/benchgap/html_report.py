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

from .fitting import fit_all, predict, select_best  # noqa: E402
from .report import mapping_summary, score_matrix  # noqa: E402

COLORS = {
    "measured": "#2563eb",
    "gapfilled": "#d97706",
    "best": "#111827",
    "candidates": ["#9ca3af", "#6b7280", "#f59e0b", "#10b981"],
    "range": "#f3f4f6",
}

METHOD_EQUATIONS = {
    "linear": "y = {slope:.4f}·x + {intercept:.4f}",
    "quadratic": "y = {a:.4f}·x² + {b:.4f}·x + {c:.4f}",
    "mm": "y = {vmax:.4f}·x / ({k:.5f} + x)",
    "mm_offset": "y = {y0:.4f} + {vmax:.4f}·x / ({k:.5f} + x)",
}


def _labels(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        r["id"]: f"{r['benchmark']}/{r['version']}"
        + (f"@{r['harness']}" if r["harness"] != "unknown" else "")
        for r in conn.execute(
            "SELECT v.id, v.version, v.harness, b.name AS benchmark"
            " FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
        )
    }


def _best_mappings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT m.* FROM mappings m"
        " WHERE m.id = ("
        "   SELECT m2.id FROM mappings m2"
        "    WHERE m2.from_version_id = m.from_version_id"
        "      AND m2.to_version_id = m.to_version_id"
        "    ORDER BY COALESCE(json_extract(m2.metrics_json, '$.LOO_RMSE'), 1e9),"
        "             COALESCE(json_extract(m2.metrics_json, '$.RMSE'), 1e9)"
        "    LIMIT 1)"
        " ORDER BY m.from_version_id, m.to_version_id"
    ).fetchall()
    return rows


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
    best = select_best(results)
    params = json.loads(mapping["params_json"])
    train_range = json.loads(mapping["train_range_json"])

    fig, (ax, ax_res) = plt.subplots(
        1, 2, figsize=(11, 4.4), gridspec_kw={"width_ratios": [3, 2]}
    )

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

    ax.scatter(xs * 100, ys * 100, s=42, color=COLORS["measured"], zorder=3,
               label=f"paired models (n={len(xs)})")
    if gapfilled:
        gx = np.array([g["input"] for g in gapfilled])
        gy = np.array([g["value"] for g in gapfilled])
        ax.scatter(gx * 100, gy * 100, s=46, facecolors="none", edgecolors=COLORS["gapfilled"],
                   lw=1.6, zorder=3, label=f"gapfilled (n={len(gx)})")
    ax.set_xlabel(f"{label_from} score (%)")
    ax.set_ylabel(f"{label_to} score (%)")
    ax.set_title(f"{label_from} → {label_to}")
    ax.legend(fontsize=7.5, loc="lower right", framealpha=0.9)
    ax.grid(alpha=0.25)

    # residuals of the best fit
    pred = predict(best.method, params, xs)
    ax_res.axhline(0, color="#d1d5db", lw=1)
    ax_res.scatter(xs * 100, (ys - pred) * 100, s=34, color=COLORS["measured"])
    for x, y0, y1 in zip(xs * 100, np.zeros_like(xs), (ys - pred) * 100):
        ax_res.plot([x, x], [0, y1], color="#cbd5e1", lw=0.8, zorder=1)
    ax_res.set_xlabel(f"{label_from} score (%)")
    ax_res.set_ylabel("residual (pp)")
    ax_res.set_title(f"residuals, {best.method}")
    ax_res.grid(alpha=0.25)

    fig.tight_layout()
    return _fig_to_data_uri(fig)


def _mapping_card(conn: sqlite3.Connection, mapping: sqlite3.Row, labels: dict[int, str]) -> str:
    params = json.loads(mapping["params_json"])
    metrics = json.loads(mapping["metrics_json"])
    eq = METHOD_EQUATIONS.get(mapping["method"], json.dumps(params))
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
    row = (f"<tr><td>n (paired models)</td><td>{mapping['n_points']}</td></tr>"
           f"<tr><td>R²</td><td>{metrics['R2']:.4f}</td></tr>"
           f"<tr><td>adj. R²</td><td>{metrics['adj_R2']:.4f}</td></tr>"
           f"<tr><td>RMSE</td><td>{metrics['RMSE'] * 100:.2f} pp</td></tr>"
           f"<tr><td>LOO-CV RMSE</td><td><b>{metrics['LOO_RMSE'] * 100:.2f} pp</b></td></tr>"
           f"<tr><td>gapfilled scores</td><td>{len(gapfilled)}</td></tr>")
    return f"""
    <section class="card">
      <h2>{label_from} → {label_to}</h2>
      <p class="equation">selected method: <b>{mapping['method']}</b> &nbsp;|&nbsp;
         <code>{eq.format(**params)}</code> &nbsp;(scores as fractions)</p>
      <img src="{fig_uri}" alt="fit for {label_from} to {label_to}">
      <table class="metrics">{row}</table>
    </section>"""


def _matrix_html(conn: sqlite3.Connection) -> str:
    columns, rows = score_matrix(conn)
    order = sorted(
        range(len(rows)),
        key=lambda i: max((c["value"] or -1) for c in rows[i]["cells"]),
        reverse=True,
    )
    head = "".join(f"<th>{c}</th>" for c in columns)
    body = []
    for i in order:
        r = rows[i]
        cells = []
        for c in r["cells"]:
            if c["value"] is None:
                cells.append('<td class="missing">–</td>')
            elif c["kind"] == "g":
                cells.append(
                    f'<td class="gapfilled" title="gapfilled: fitted mapping,'
                    f' not a measured score">{c["value"]:.1f}</td>'
                )
            else:
                cells.append(f"<td>{c['value']:.1f}</td>")
        body.append(f"<tr><th class='rowhead'>{r['slug']}</th>{''.join(cells)}</tr>")
    return (
        "<table class='matrix'><tr><th class='rowhead'>model</th>"
        f"{head}</tr>{''.join(body)}</table>"
    )


def generate_html_report(conn: sqlite3.Connection, path: str | Path) -> Path:
    """Write a self-contained HTML report; returns the output path."""
    labels = _labels(conn)
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
    mappings = _best_mappings(conn)
    cards = "".join(_mapping_card(conn, m, labels) for m in mappings)
    summary_rows = "".join(
        f"<tr><td>{s['from']}</td><td>{s['to']}</td><td><b>{s['method']}</b></td>"
        f"<td>{s['n_pairs']}</td><td>{s['R2']:.3f}</td>"
        f"<td>{s['LOO_RMSE_pp']:.2f}</td></tr>"
        for s in summary
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
         max-width: 1080px; margin: 2rem auto; padding: 0 1.5rem; background: #fff; }}
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
  .card {{ border: 1px solid var(--line); border-radius: 10px; padding: 1rem 1.4rem;
          margin: 1.5rem 0; }}
  .card h2 {{ margin-top: 0; font-size: 1.05rem; }}
  .card img {{ width: 100%; height: auto; }}
  .equation {{ color: var(--muted); }}
  code {{ background: var(--bg); padding: .1rem .35rem; border-radius: 4px; }}
  .metrics td:first-child {{ color: var(--muted); }}
  .matrix td, .matrix th {{ text-align: right; }}
  .matrix .rowhead {{ text-align: left; font-weight: 500; }}
  .matrix td.gapfilled {{ color: #b45309; background: #fffbeb; }}
  .matrix td.missing {{ color: #d1d5db; }}
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

<h2>Fitted mappings</h2>
<table>
<thead><tr><th>from</th><th>to</th><th>selected method</th><th>n pairs</th>
<th>R²</th><th>LOO RMSE (pp)</th></tr></thead>
<tbody>{summary_rows}</tbody>
</table>
<p class="legend">Selection is by leave-one-out cross-validated RMSE among the fitted
candidates (linear, quadratic, Michaelis–Menten, Michaelis–Menten with offset).
Solid curves in the figures are the selected fit; gray curves are the alternatives.</p>

{cards}

<h2>Score matrix (percent)</h2>
{_matrix_html(conn)}
<p class="legend">Amber cells are <b>gapfilled</b>: values predicted by the fitted
mapping, not measured scores. Hover a cell for details. Models are ordered by
their best score.</p>

<footer>
Coefficients are harness-specific: the seed pairs come from the Artificial
Analysis harness (Terminal-Bench 2.1 vs 4.0), not the official tbench.ai
leaderboards. Gapfilled values outside the fitted training range are flagged
as extrapolated in the database. Probabilistic fits (credible intervals) are
planned; the schema already carries ci95 columns.
</footer>
</body>
</html>"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
