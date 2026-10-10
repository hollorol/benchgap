/* The calibrations: which benchmarks predict which (/calibration), and one fitted mapping (/calibration/<id>). */
import { html, svg, render, nothing } from "../vendor/lit-html.js";
import { D, ix, $, pct, load, remember, methodLabel, capLabel, capOf, mappingHref, setMeta, show } from "../core.js";
import { dot, modelLink, confidenceFlag, pageHead, axis, swap, lossColor, LOSS_RAMP, capFilter, renderNotFound } from "../ui.js";

// data: the calibrations between listed benchmarks (data/calibration.json); the cross-domain fits load
// after the page, dropped if it was left meanwhile (signal)
export function renderCalibration(data, arg, signal) {
  setMeta("LLM benchmark calibrations", "Which LLM benchmarks predict which: the fitted cross-benchmark calibrations behind every estimate, with their errors.");
  const gate = D.meta.quality_gate.max_loo_pp;
  // one capability at a time (the one with the most mappings first)
  const maps = data.mappings;
  const byPair = new Map(maps.map((m) => [m.from + ":" + m.to, m]));
  const filter = capFilter(maps, (m) => capOf(m.from)), nByCap = filter.byCap, caps = filter.caps;
  const capName = (cap) => (cap === "all" ? "All capabilities" : capLabel(cap));
  // the cross-domain fits (data/cross.json), by "from:to"; loaded after the page
  let cross = null;
  const LIST_ROWS = 20;  // mappings listed before "Show all"

  // a source x target table of rows ({ id, label }) with cell(src, dst, pair) for each cell
  const pmTable = (rows, cell) => html`<table class="pm"><thead><tr><th style="text-align:right;vertical-align:bottom" class="muted">source ↓ · target →</th>${rows
    .map((r) => html`<th><span class="colh">${r.label}</span></th>`)}</tr></thead><tbody>${rows
    .map((src) => html`<tr><th scope="row">${src.label}</th>${rows.map((dst) => cell(src, dst, `${src.label} → ${dst.label}`))}</tr>`)}</tbody></table>`;

  function pairMap(cap) {
    const involved = new Set();
    nByCap.get(cap).forEach((m) => { involved.add(m.from); involved.add(m.to); });
    // grouped by capability (stable: within one, the site's order)
    const vs = D.benchmarks.filter((b) => involved.has(b.id));   // already in capability order
    return pmTable(vs, (src, dst, pair) => {
      if (src === dst) return html`<td class="diag"></td>`;
      if (src.capability !== dst.capability) {
        const x = cross?.get(src.id + ":" + dst.id);
        if (x?.passes && x.loo != null) return html`<td class="cell cross" style="background:${lossColor(x.loo * 100, gate)}" data-tiptext="${pair}: cross-domain, ${methodLabel(x.method)}, n=${x.n}, R²=${x.r2.toFixed(2)}, LOO error ${pct(x.loo)} pp (shown to compare, never used for estimates)">${pct(x.loo)}</td>`;
        return html`<td data-tiptext="${pair}: ${x ? "the cross-domain fit fails the quality gate" : "different capabilities, too few shared models"}"></td>`;
      }
      const m = byPair.get(src.id + ":" + dst.id);
      if (!m) return html`<td class="none" data-tiptext="${pair}: no usable mapping (too few shared models, or the best fit failed the quality gate)"></td>`;
      return html`<td class="cell" style="background:${lossColor(m.loo * 100, gate)}"><a href="${mappingHref(m.id)}" data-tiptext="${pair}: ${methodLabel(m.method)}, n=${m.n}, R²=${m.r2.toFixed(2)}, LOO error ${pct(m.loo)} pp, used for ${m.n_used} estimates">${pct(m.loo)}</a></td>`;
    });
  }
  // each capability's pair map is built once, and the chips switch between the built tables
  // (All's again when the cross-domain fits come in): the big ones take long to build
  const pairMaps = new Map();
  function pairMapOf(cap) {
    if (!pairMaps.has(cap)) {
      const box = document.createElement("div");
      render(pairMap(cap), box);
      pairMaps.set(cap, box.querySelector("table"));
    }
    return pairMaps.get(cap);
  }
  function mapList(cap, all) {
    const rows = nByCap.get(cap);   // lowest error first (data/calibration.json)
    return html`<table class="list maps"><thead><tr><th>Mapping</th><th>Selected curve</th><th style="text-align:right">n</th><th style="text-align:right">R²</th><th style="text-align:right">LOO error (pp)</th><th style="text-align:right">Estimates</th></tr></thead><tbody>${(all ? rows : rows.slice(0, LIST_ROWS))
      .map((m) => {
        const f = ix.bench.get(m.from), t = ix.bench.get(m.to);
        return html`<tr><td><a href="${mappingHref(m.id)}">${f.label} → ${t.label}</a></td><td>${methodLabel(m.method)}</td>
            <td class="num">${m.n}</td><td class="num">${m.r2.toFixed(3)}</td><td class="num err"><span class="scale-dot" style="background:${lossColor(m.loo * 100, gate)}"></span>${pct(m.loo)}</td><td class="num">${m.n_used}</td></tr>`;
      })}</tbody></table>${!all && rows.length > LIST_ROWS ? html`<button type="button" class="btn more-maps" @click=${() => swap($("#maps"), mapList(cap, true))}>Show all ${rows.length} mappings</button>` : nothing}`;
  }

  // capability x capability: the median LOO error of the cross-domain fits that pass the gate (data/cross.json summary)
  function crossTable(summary) {
    return pmTable(caps.slice(1), (src, dst, pair) => {
      if (src === dst) return html`<td class="diag" data-tiptext="${src.label}: the same capability, calibrated within it (its chip above)"></td>`;
      const c = summary[src.id + ":" + dst.id];
      if (!c?.n_pass) return html`<td class="none" data-tiptext="${pair}: ${c ? `none of ${c.n} benchmark pairs passes the quality gate` : "no benchmark pairs with enough shared models"}"></td>`;
      const [from, to, loo] = c.best;
      return html`<td class="cell cross" style="background:${lossColor(c.median * 100, gate)}" data-tiptext="${pair}: median LOO error ${pct(c.median)} pp; ${c.n_pass} of ${c.n} benchmark pairs pass the quality gate; best ${ix.bench.get(from).label} → ${ix.bench.get(to).label} (${pct(loo)} pp)">${pct(c.median)}</td>`;
    });
  }

  let cap = caps.slice(1).reduce((a, c) => (c.n > a.n ? c : a)).id;
  // a chip redraws the pair map and the list for its capability (and draws them first)
  const pick = (id) => {
    cap = id;
    draw();
    swap($("#pm-wrap"), pairMapOf(cap));
    swap($("#maps-cap"), capName(cap));
    swap($("#maps"), mapList(cap, false));
  };
  // the page; the pair map, the cross-domain table and the list are drawn into it on their own (swap())
  const draw = () => show(html`<div class="page">
      ${pageHead("Calibration", "Which benchmarks predict which", `For each ordered pair of same-capability benchmarks with at least ${D.meta.quality_gate.min_pairs} shared models,
        several monotone curves are fitted and the one with the lowest leave-one-out error is kept, if it passes the quality gate
        (R² ≥ ${D.meta.quality_gate.min_r2}, error ≤ ${gate} pp). Cells show that error in percentage points: rows are the source, columns the target.
        Estimates come only from calibrations within a capability; pick one below, or All, which also shows the fits across capabilities (see Cross-domain predictability).`)}
      <section class="section">
        ${filter.chips(cap, pick)}
        <div class="pm-wrap" id="pm-wrap"></div>
        <div class="scale"><span>0 pp</span><span class="ramp" style="background:${LOSS_RAMP}"></span><span>${gate} pp (gate)</span>
          <span style="margin-left:1rem"><span class="sq none"></span>no usable mapping</span>
          <span><span class="sq diag"></span>same benchmark</span>
          <span><span class="sq cross"></span>cross-domain (All only)</span></div>
      </section>
      <section class="section">
        <h2 class="h2">Cross-domain predictability</h2>
        <p class="lede">How well one capability's benchmarks predict another's: each cell is the median leave-one-out error (pp)
          of the fits between their benchmarks that pass the quality gate, from source capability (row) to target (column).
          Fits across capabilities are made only for this comparison and the All view; no estimate comes from them.</p>
        <div class="pm-wrap" id="cross-sum"></div>
      </section>
      <section class="section">
        <h2 class="h2">Fitted mappings · <span id="maps-cap"></span></h2>
        <div class="list-wrap" id="maps"></div>
      </section></div>`);
  pick(cap);
  const sum = $("#cross-sum");
  render(html`<p class="muted">Loading the cross-domain fits…</p>`, sum);
  load("/data/cross.json").then((d) => {
    if (signal.aborted) return;   // another page was opened meanwhile
    cross = new Map(d.cross.map(([from, to, method, n, r2, loo, passes]) => [from + ":" + to, { from, to, method, n, r2, loo, passes }]));
    if (!d.cross.length) { render(html`<p class="muted">No cross-domain fits in this build yet.</p>`, sum); return; }
    swap(sum, crossTable(d.summary));
    pairMaps.delete("all");
    if (cap === "all") swap($("#pm-wrap"), pairMapOf(cap));
  }).catch(() => { if (!signal.aborted) render(html`<p class="muted">The cross-domain fits could not be loaded.</p>`, sum); });
}

// data: the calibration with its points and curve, the estimates it made and its reverse (data/calibration/{id}.json)
export function renderMapping(data) {
  if (!data) return renderNotFound("No such mapping.");
  const m = data.mapping, reverse = data.reverse;
  const f = ix.bench.get(m.from), t = ix.bench.get(m.to);
  setMeta(`${f.label} → ${t.label} calibration`, `How ${f.label} scores predict ${t.label}: the fitted ${methodLabel(m.method)} curve, the models it was fitted on and its cross-validated error.`);
  const ests = remember(data.estimates);

  // plot geometry
  const W = 720, H = 460, L = 56, R = 18, T = 18, B = 50;
  const xs = m.points.map((p) => p[1]).concat(ests.map((s) => s.via.from[0].v));
  const ys = m.points.map((p) => p[2]).concat(ests.map((s) => s.v));
  const xAxis = axis(Math.max(...xs) + 0.02), yAxis = axis(Math.max(...ys) + 0.02);
  const xMax = xAxis.max, yMax = yAxis.max;
  const px = (x) => L + (x / xMax) * (W - L - R);
  const py = (y) => H - B - (y / yMax) * (H - T - B);
  const grid = [
    ...xAxis.ticks.map((v) => svg`<line class="gl" x1="${px(v)}" x2="${px(v)}" y1="${T}" y2="${H - B}"/><text x="${px(v)}" y="${H - B + 18}" text-anchor="middle">${Math.round(v * 100)}</text>`),
    ...yAxis.ticks.map((v) => svg`<line class="gl" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/><text x="${L - 8}" y="${py(v) + 4}" text-anchor="end">${Math.round(v * 100)}</text>`),
  ];
  const [r0, r1] = m.range || [0, xMax];
  // polyline through the exported curve samples with a <= x <= b
  const path = (a, b) => {
    const pts = m.curve
      .filter(([x]) => x >= a && x <= b && x <= xMax)
      .map(([x, y]) => `${px(x).toFixed(1)},${py(Math.max(0, Math.min(yMax, y))).toFixed(1)}`);
    return pts.length > 1 ? "M" + pts.join("L") : "";
  };
  const pts = m.points.map((p) => {
    const mod = ix.model.get(p[0]);
    return svg`<circle class="pt" cx="${px(p[1])}" cy="${py(p[2])}" r="4.5"/><circle class="hit" cx="${px(p[1])}" cy="${py(p[2])}" r="9" data-tiptext="${mod ? mod.name : "?"}: ${pct(p[1])}% → ${pct(p[2])}% (measured on both)"/>`;
  });
  const epts = ests.map((s) => {
    const x = s.via.from[0].v;
    return svg`<circle class="ept ${s.tier}" cx="${px(x)}" cy="${py(s.v)}" r="4.5"/><circle class="hit" cx="${px(x)}" cy="${py(s.v)}" r="9" data-tip="${s.m}:${s.b}"/>`;
  });
  const plot = html`<svg class="plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="Scatter of ${f.label} against ${t.label} with the fitted ${methodLabel(m.method)} curve">
      <rect class="band" x="${px(r0)}" y="${T}" width="${px(r1) - px(r0)}" height="${H - T - B}"/>
      ${grid}
      <line class="ax" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><line class="ax" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
      <path class="curve-out" d="${path(0, r0)}"/><path class="curve-out" d="${path(r1, xMax)}"/>
      <path class="curve" d="${path(r0, r1)}"/>
      ${pts}${epts}
      <text class="lbl" x="${(L + W - R) / 2}" y="${H - 10}" text-anchor="middle">${f.label} score (%)</text>
      <text class="lbl" transform="translate(16 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">${t.label} score (%)</text>
    </svg>`;

  const estRows = ests.map((s) => {   // highest first (data/calibration/{id}.json)
    const mod = ix.model.get(s.m);
    return html`<tr><td>${dot(mod)} ${modelLink(mod)}</td><td class="num">${pct(s.via.from[0].v)}%</td><td class="num"><i>≈${pct(s.v)}%</i></td><td>${confidenceFlag(s.tier)}${s.x ? html` <span class="flag x">extrapolated</span>` : nothing}</td></tr>`;
  });

  show(html`<div class="page">
      ${pageHead(html`<a href="/calibration">Calibration</a> · ${capLabel(f.capability)}`, html`${f.label} <span class="muted">→</span> ${t.label}`,
        html`Each dot is a model measured on both benchmarks. The curve is the selected
        ${methodLabel(m.method)} fit; the shaded band is the range of ${f.label} scores it was fitted on,
        and the dashed parts are extrapolation. Hollow rings are the estimates it produced, coloured by confidence.`)}
      <div class="grid-2 section">
        <div class="card">${plot}
          <div class="legend" style="border:0;padding-bottom:0">
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="currentColor"/></svg> measured on both</span>
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-high)" stroke-width="2"/></svg> high</span>
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-medium)" stroke-width="2"/></svg> medium</span>
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-low)" stroke-width="2" stroke-dasharray="2 1.5"/></svg> low confidence</span>
          </div>
        </div>
        <div>
          <div class="eqn">${m.equation}</div>
          <p class="muted" style="font-size:.8rem;margin:.4rem 0 0">x and y as fractions (0–1).</p>
          <dl class="kv" style="margin-top:1rem">
            <dt>paired models</dt><dd>${m.n}</dd>
            <dt>R²</dt><dd>${m.r2.toFixed(3)}</dd>
            <dt>in-sample RMSE</dt><dd>${pct(m.rmse)} pp</dd>
            <dt>LOO-CV error</dt><dd><b>${pct(m.loo)} pp</b></dd>
            <dt>fitted x-range</dt><dd>${m.range ? `${pct(m.range[0])}–${pct(m.range[1])}%` : "—"}</dd>
            <dt>estimates made</dt><dd>${ests.length}</dd>
          </dl>
          ${reverse ? html`<p style="margin-top:1rem"><a class="btn-link" href="${mappingHref(reverse.id)}">Reverse direction: ${t.label} → ${f.label} (${pct(reverse.loo)} pp) →</a></p>` : nothing}
          ${estRows.length ? html`<div class="list-wrap" style="margin-top:1rem"><table class="list"><thead><tr><th>Estimated model</th><th style="text-align:right">${f.label}</th><th style="text-align:right">${t.label}</th><th>Confidence</th></tr></thead><tbody>${estRows}</tbody></table></div>` : nothing}
        </div>
      </div></div>`);
}
