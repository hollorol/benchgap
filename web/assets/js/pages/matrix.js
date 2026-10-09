/* The score matrix: every model × every benchmark. */
import { html, render, nothing, repeat, guard } from "../vendor/lit-html.js";
import { D, ix, prefs, store, $, pct, groupBy, remember, visible, capLabel, modelHref, setMeta, show } from "../core.js";
import { hideTip, hideStaleTip } from "../tip.js";
import { dot, pageHead, legend, showSeg, phone } from "../ui.js";

// a matrix cell's kind (src/Site.php CELL_KINDS): measured, or an estimate's confidence
const CELL_TIERS = [null, "high", "medium", "low"];
let mxd = null;   // the matrix's cells by model, each benchmark's range of measured scores (for the tint), the columns

// data: every listed benchmark's cells (data/matrix.json), as [model, benchmark, value, kind]; an
// estimate's cell is partial: where it came from loads when its tooltip opens (tip.js details())
export function renderMatrix(data) {
  setMeta("LLM benchmark score matrix", "Every model on every benchmark: measured LLM scores and calibrated estimates for the missing ones, side by side.");
  const cells = remember(data.cells.map(([m, b, v, k]) => (k ? { m, b, v, s: "e", tier: CELL_TIERS[k], partial: true } : { m, b, v, s: "m" })));
  // each column's measured range, and the rows' default order (most measured first)
  // and the columns, all or the dense core: the same lists every time, so a redraw that keeps them keeps the rows
  mxd = { byModel: groupBy(cells, (c) => c.m), range: data.range, order: data.models.map((id) => ix.model.get(id)),
    benches: { all: ix.listed, dense: ix.listed.filter((b) => b.dense) } };
  mx = null;
  drawMatrix();
}

// the page around the table: its controls and counts
const matrixPage = (counts) => html`<div class="page">
      ${pageHead("Score matrix", "Every model × every benchmark", html`Measured cells are tinted by score within each column. Hatched italic cells are estimates;
        a red corner marks a <b>low-confidence</b> estimate. Dots are gaps that stay gaps: no calibrated source to estimate from.
        Click a column header to rank by that benchmark.`)}
      <div class="controls">
        ${showSeg(drawMatrix)}
        <label class="check"><input type="checkbox" id="dense" .checked=${prefs.dense} @change=${(e) => { prefs.dense = e.target.checked; store.set("dense", prefs.dense); drawMatrix(); }}> Dense core only
          <span class="muted" data-tiptext="Hides benchmarks with fewer than ${D.meta.dense.min_models} measured models and models measured on fewer than ${D.meta.dense.min_benchmarks} benchmarks, peeled repeatedly until both hold. Display only.">ⓘ</span></label>
        <span class="muted mono" style="font-size:.78rem" id="mx-counts">${counts}</span>
      </div>
      ${legend()}
      <p class="mx-hint"><span>Model names stay put; the scores scroll.</span><b>Swipe →</b></p>
      <div class="matrix-wrap" id="mx-wrap" style="margin-top:1rem" @scroll=${onScroll} @click=${onSortClick} @keydown=${onSortKey}><table class="mx"><thead></thead><tbody></tbody></table></div></div>`;

// new rows as the box scrolls, at most once a frame
let ticking = false;
const onScroll = {
  handleEvent() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; paintMatrix(false); });
  },
  passive: true,
};
// a column header ranks the models by its benchmark; again, back to the default order
const sortBy = (el) => { const id = Number(el.dataset.sort); prefs.sortCol = prefs.sortCol === id ? null : id; drawMatrix(); };
function onSortClick(e) { const el = e.target.closest("[data-sort]"); if (el) sortBy(el); }
function onSortKey(e) {
  const el = e.target.closest("[data-sort]");
  if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortBy(el); }
}

// The matrix draws only the cells in view (plus a margin) inside its scroll box: tens of
// thousands of cells at once make every toggle, sort and scroll stall. Spacer rows and cells
// keep the scroll size; rows and columns each have one fixed size, so position = index × size.
// It redraws only when the view nears the edge of what is drawn, not on every scrolled frame;
// rows and cells keep their elements (by model and benchmark), and a row is drawn again only
// if its columns, its cells' size or the "Show" setting changed: scrolling adds the rows and
// cells coming into view and drops the ones leaving it, and sorting only moves rows.
const MX_ROWS = 24, MX_COLS = 10;   // rows and columns drawn beyond each edge of the view
const MX_EDGE = 6;                  // redraw when the view comes this close to the drawn edge
let mx = null;   // the table's models, benchmarks, cell sizes and the drawn window

function drawMatrix() {
  const benches = prefs.dense ? mxd.benches.dense : mxd.benches.all;
  let models = mxd.order.filter((m) => !prefs.dense || m.dense);

  const cellOf = (m, b) => {
    const s = ix.cell.get(m.id + ":" + b.id);
    return s && visible(s) ? s : null;
  };
  // one pass over each model's own scores: the counts (over every cell, drawn or not), and
  // which models have something to show here (with this "Show" setting): the others get no row
  const drawn = new Set(benches.map((b) => b.id));
  let shown = 0, est = 0, low = 0;
  models = models.filter((m) => {
    let any = false;
    for (const s of mxd.byModel.get(m.id) || []) {
      if (!drawn.has(s.b) || !visible(s)) continue;
      any = true; shown++;
      if (s.s === "e") { est++; if (s.tier === "low") low++; }
    }
    return any;
  });
  if (prefs.sortCol && benches.some((b) => b.id === prefs.sortCol)) {
    const b = ix.bench.get(prefs.sortCol);
    models = models.slice().sort((p, q) => {
      const a = cellOf(p, b), c = cellOf(q, b);
      return (c ? c.v : -1) - (a ? a.v : -1) || p.name.localeCompare(q.name);
    });
  }

  const gaps = models.length * benches.length - shown;
  hideTip();
  show(matrixPage(html`${models.length} models · ${benches.length} benchmarks · ${shown - est} measured · <i>${est} estimated</i> (${low} low confidence) · ${gaps} gaps`));
  mx = { models, benches, cellOf, rowH: mx ? mx.rowH : 0, colW: mx ? mx.colW : 0, nameW: 0, win: null };
  paintMatrix(true);
}

// a capability's first column gets a heavier left border
const mxBrk = (benches, j) => (j > 0 && benches[j].capability !== benches[j - 1].capability ? " brk" : "");
// spacer cells for columns not drawn
const padTh = (n, colW) => (n > 0 ? html`<th class="mx-padc" colspan="${n}" style="width:${n * colW}px;min-width:${n * colW}px"></th>` : nothing);
const padTd = (n, colW) => (n > 0 ? html`<td class="mx-padc" colspan="${n}" style="width:${n * colW}px;min-width:${n * colW}px"></td>` : nothing);

// colW: the columns' width (spacers stand in for the ones not drawn)
function matrixHead(c0, c1, colW) {
  const { benches } = mx;
  // the capability spans of the drawn columns
  const spans = [];
  for (let j = c0; j < c1; j++) {
    const last = spans[spans.length - 1];
    if (last && last.cap === benches[j].capability) last.n++;
    else spans.push({ cap: benches[j].capability, n: 1, j });
  }
  return html`<tr class="caps"><th class="corner" rowspan="2">Model</th>${padTh(c0, colW)}${spans
      .map((sp) => html`<th colspan="${sp.n}" class="${mxBrk(benches, sp.j).trim()}" title="${capLabel(sp.cap)}"><span class="cap-l">${capLabel(sp.cap)}</span></th>`)}${padTh(benches.length - c1, colW)}</tr>
    <tr class="cols">${padTh(c0, colW)}${benches.slice(c0, c1)
      .map((b, k) => html`<th class="${prefs.sortCol === b.id ? "sorted" : ""}${mxBrk(benches, c0 + k)}"><span class="colh" data-sort="${b.id}" role="button" tabindex="0" title="Sort by ${b.label}">${b.label}</span></th>`)}${padTh(benches.length - c1, colW)}</tr>`;
}

function matrixCell(m, b, j) {
  const s = mx.cellOf(m, b), brk = mxBrk(mx.benches, j);
  if (!s) return html`<td class="gap${brk}">·</td>`;
  if (s.s === "e") return html`<td class="e ${s.tier}${brk}" data-tip="${m.id}:${b.id}" tabindex="0">${pct(s.v, 0)}</td>`;
  const [lo, hi] = mxd.range[b.id];
  const h = hi > lo ? (s.v - lo) / (hi - lo) : 0.5;
  return html`<td class="m${brk}" style="--h:${(0.15 + h * 0.85).toFixed(2)}" data-tip="${m.id}:${b.id}">${pct(s.v, 0)}</td>`;
}

function matrixRow(m, c0, c1, colW) {
  const { benches } = mx;
  return html`<tr><th scope="row"><a href="${modelHref(m)}" title="${m.name}">${dot(m)}<span>${m.name}</span></a></th>${padTd(c0, colW)}${repeat(benches.slice(c0, c1), (b) => b.id, (b, k) => matrixCell(m, b, c0 + k))}${padTd(benches.length - c1, colW)}</tr>`;
}

// draws the cells around the view, if the view has come near the edge of what is drawn (or force)
function paintMatrix(force) {
  const wrap = $("#mx-wrap"), table = wrap && wrap.querySelector("table");
  if (!mx || !table) return;
  const nR = mx.models.length, nC = mx.benches.length;
  // first guesses; the first drawing measures them
  const rowH = mx.rowH || (phone.matches ? 41 : 31), colW = mx.colW || (phone.matches ? 44 : 46);
  const head = table.tHead, nameW = mx.nameW || (phone.matches ? 136 : 230);
  const top = Math.max(0, wrap.scrollTop - head.offsetHeight);
  const left = Math.max(0, wrap.scrollLeft);
  // the rows and columns in view
  const r0 = Math.floor(top / rowH), r1 = Math.ceil((top + wrap.clientHeight) / rowH);
  const v0 = Math.floor(left / colW), v1 = Math.ceil((left + wrap.clientWidth - nameW) / colW);
  const w = mx.win;
  const inside = w && r0 >= w.r0 + (w.r0 > 0 ? MX_EDGE : 0) && r1 <= w.r1 - (w.r1 < nR ? MX_EDGE : 0)
    && v0 >= w.c0 + (w.c0 > 0 ? MX_EDGE / 2 : 0) && v1 <= w.c1 - (w.c1 < nC ? MX_EDGE / 2 : 0);
  if (!force && inside) return;
  const win = {
    r0: Math.max(0, r0 - MX_ROWS), r1: Math.min(nR, r1 + MX_ROWS),
    c0: Math.max(0, v0 - MX_COLS), c1: Math.min(nC, v1 + MX_COLS),
  };
  mx.win = win;
  render(guard([mx.benches, win.c0, win.c1, colW, prefs.sortCol], () => matrixHead(win.c0, win.c1, colW)), head);
  const pad = (rows) => (rows > 0 ? html`<tr class="mx-pad" aria-hidden="true"><td colspan="${nC + 1}" style="height:${rows * rowH}px"></td></tr>` : nothing);
  render(html`${pad(win.r0)}${repeat(mx.models.slice(win.r0, win.r1), (m) => m.id, (m) => guard([mx.benches, win.c0, win.c1, colW, prefs.show], () => matrixRow(m, win.c0, win.c1, colW)))}${pad(nR - win.r1)}`, table.tBodies[0]);
  hideStaleTip();
  if (!force) return;
  // a full drawing measures the real sizes (scrolling never changes them); draw again if the guess was off
  const row = table.tBodies[0].querySelector("tr:not(.mx-pad)"), cell = row && row.querySelector("td:not(.mx-padc)");
  mx.nameW = head.querySelector(".corner").offsetWidth;
  // the capability labels stop at the model column's edge while the columns scroll under it
  wrap.style.setProperty("--name-w", mx.nameW + "px");
  const off = row && (Math.abs(row.offsetHeight - rowH) > 0.5 || Math.abs(cell.offsetWidth - colW) > 0.5);
  mx.rowH = row ? row.offsetHeight : rowH;
  mx.colW = cell ? cell.offsetWidth : colW;
  if (off) paintMatrix(true);
}
