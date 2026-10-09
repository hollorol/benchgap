/* The leaderboard: one benchmark's scores, measured and estimated, highest first. */
import { html, nothing, repeat, guard } from "../vendor/lit-html.js";
import { ABOUT, D, ix, prefs, $, main, pct, docs, boardUrl, remember, capLabel, visible, benchHref, modelHref, setMeta, show, go } from "../core.js";
import { hideTip } from "../tip.js";
import { legend, showSeg, tierFlag, track, axis, reduceMotion, ease, toggleList, centerIn, renderNotFound } from "../ui.js";

// a benchmark's model counts: measured, plus estimated if any
const benchCount = (x) => `${x.n_measured}${x.n_estimated ? "+" + x.n_estimated : ""}`;
// a benchmark's chip, current if it is b
const benchChip = (x, b) => html`<a class="chip" href="${benchHref(x)}" aria-current="${x.id === b.id}">${x.label}<span class="cnt">${benchCount(x)}</span></a>`;
const MAX_CHIPS = 8;  // chips per capability on the leaderboard picker and the phones' rail
// a capability's benchmarks in the picker's order (site.json picker: the original ones first, then the most measured)
const pickerOrder = (cap) => (D.picker[cap] || []).map((id) => ix.bench.get(id));
// a capability's chips: up to MAX_CHIPS in the picker's order, and the open benchmark b
// if it belongs to the capability but is not among them
function capChips(cap, b, order = pickerOrder(cap)) {
  const chips = order.slice(0, MAX_CHIPS);
  if (b.capability === cap && !chips.includes(b)) chips.push(b);
  return chips;
}
// each capability's chips; the rest folds into one "+N more" list
const picker = (b) => html`<nav class="picker" aria-label="Benchmarks">${ix.benchesByCap.map(({ cap }) => {
  const order = pickerOrder(cap.id), chips = capChips(cap.id, b, order);
  const rest = order.filter((x) => !chips.includes(x));
  const more = rest.length ? html`<button type="button" class="chip more-btn" aria-expanded="false" aria-controls="more-${cap.id}">+${rest.length} more</button>
        <div class="more-list" id="more-${cap.id}" hidden><p class="more-note">By models measured · faint ones have fewer than 5</p>${rest
          .map((x) => html`<a class="more-item${x.n_measured < 5 ? " few" : ""}" href="${benchHref(x)}" aria-current="${x.id === b.id}"><span>${x.label}</span><span class="cnt">${benchCount(x)}</span></a>`)}</div>` : nothing;
  return html`<div class="picker-row"><div class="cap">${cap.label}</div><div class="chips">${chips.map((x) => benchChip(x, b))}${more}</div></div>`;
})}</nav>`;

// a "+N more" button opens its list and closes any other; Escape closes it
const setMore = (btn, open) => {
  btn.setAttribute("aria-expanded", String(open));
  toggleList(document.getElementById(btn.getAttribute("aria-controls")), open);
};
const closeMore = (except) => main.querySelectorAll('.more-btn[aria-expanded="true"]').forEach((btn) => {
  if (btn !== except) setMore(btn, false);
});
document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest(".more-btn");
  if (!btn) return;
  const open = btn.getAttribute("aria-expanded") !== "true";
  closeMore(btn);
  setMore(btn, open);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMore(); });

// phones: the open benchmark's capability as a swipeable row of the same chips as the picker
function rail(b) {
  const benches = capChips(b.capability, b);
  if (benches.length < 2) return nothing;
  return html`<p class="rail-cap">Also in <b>${capLabel(b.capability)}</b></p><nav class="rail chip-row" aria-label="${capLabel(b.capability)} benchmarks">${benches
    .map((x) => benchChip(x, b))}</nav>`;
}
const centerRail = () => centerIn($("#bench-rail .rail"), '[aria-current="true"]');

// phones: the benchmarks as one <select>, the open one selected
const pickerMobile = (b) => html`<div class="picker-mobile"><label><span class="ctl-label">Benchmark · ${D.meta.counts.benchmarks} to pick from</span><select class="select" id="bench-select" @change=${(e) => go(benchHref({ key: e.target.value }))}>${b.listed ? nothing : html`<option value="${b.key}" selected>${b.label}</option>`}${ix.benchesByCap
    .map(({ cap, benches }) => html`<optgroup label="${cap.label}">${benches
      .map((x) => html`<option value="${x.key}" .selected=${x.id === b.id}>${x.label} (${x.n_measured}${x.n_estimated ? " + " + x.n_estimated + " est." : ""})</option>`)}</optgroup>`)}</select></label><div id="bench-rail">${rail(b)}</div></div>`;

function hero() {
  const c = D.meta.counts;
  const t = c.confidence;
  const tot = Math.max(1, t.high + t.medium + t.low);
  return html`
      <section class="hero reveal">
        <div>
          <div class="eyebrow">LLM benchmark leaderboard · gaps filled</div>
          <h1 class="display" style="margin-top:.9rem">Mind the <em>gap</em>.</h1>
          <p class="lede">${ABOUT} Estimates are hatched and italic; low-confidence ones are flagged.</p>
        </div>
        <div class="ledger">
          <div><div class="n">${c.models}</div><div class="k">models</div></div>
          <div><div class="n">${c.benchmarks}</div><div class="k">benchmark versions</div></div>
          <div><div class="n">${c.measured}</div><div class="k">measured scores</div></div>
          <div><div class="n est">${c.estimated}</div><div class="k">estimated scores</div>
            <div class="tierbar" aria-hidden="true">
              <span class="t-high" style="width:${(t.high / tot) * 100}%"></span>
              <span class="t-medium" style="width:${(t.medium / tot) * 100}%"></span>
              <span class="t-low" style="width:${(t.low / tot) * 100}%"></span>
            </div>
            <div class="tierkey"><span><b>${t.high}</b> high</span><span><b>${t.medium}</b> medium</span><span><b>${t.low}</b> low confidence</span></div>
          </div>
        </div>
      </section>`;
}

// "As of …, the highest measured score on X is …": the leaderboard's lede (Pages::benchmarkLead)
function benchLead(b, scores) {
  const top = scores.find((s) => s.s === "m");   // scores come highest first
  const n = b.n_estimated;
  return [
    top ? `As of ${D.meta.retrieved_at}, the highest measured score on ${b.label} is ${pct(top.v)}% by ${ix.model.get(top.m).name}.` : "",
    n ? `${n === 1 ? "1 more model has an estimated score" : `${n} more models have estimated scores`}, calibrated from the benchmarks they were measured on.` : "",
  ].filter(Boolean).join(" ");
}

let board = null;   // the open benchmark and its scores

// A leaderboard; data: its scores (data/b/...), key: its benchmark (none: the home page's).
// Returns true when it was already open (only the benchmark changed).
export function renderBoard(data, key) {
  key ||= ix.home;
  const b = ix.benchByKey.get(key);
  if (!b || !data) return renderNotFound(`No benchmark “${key}”.`);
  if (!docs.has(boardUrl(key))) docs.set(boardUrl(key), Promise.resolve(data));   // the home page's, under its own name too
  const scores = remember(data.scores);
  if (key === ix.home) setMeta("LLM Benchmark Leaderboard with Estimated Scores",
    "LLM benchmark scores: measured where available, estimated where missing, with every estimate's error and confidence.", "/");
  else setMeta(`${b.label} leaderboard`, `${b.label} leaderboard: ${b.n_measured} measured and ${b.n_estimated} estimated LLM scores, each estimate with its error and confidence.`);
  const inPlace = !!$("#board-sec");
  board = { b, scores };
  draw();
  if (inPlace) closeMore();
  centerRail();
  return inPlace;
}

// the page; the hero and the pickers are drawn again only for another benchmark
function draw() {
  const { b, scores } = board;
  show(html`<div class="page">${guard([], hero)}${guard([b], () => picker(b))}${guard([b], () => pickerMobile(b))}<section id="board-sec">${boardBody(b, scores)}</section></div>`);
}

// header, controls and legend for one benchmark and its scores, then the rows
function boardBody(b, all) {
  const nEst = b.n_estimated;
  const nLow = all.filter((s) => s.s === "e" && s.tier === "low").length;
  return html`
      <div class="board-head">
        <div>
          <div class="eyebrow">${capLabel(b.capability)}</div>
          <h2 class="h2" style="margin-top:.4rem">${b.label}</h2>
        </div>
        <div class="src">${b.n_measured} measured · <i>${nEst} estimated</i>${nLow ? ` (${nLow} low confidence)` : ""}
          ${b.source_url ? html` · source: <a href="${b.source_url}" rel="noopener" target="_blank">${host(b.source_url)}</a>` : nothing}</div>
      </div>
      <p class="lede board-lead">${benchLead(b, all)}</p>
      <div class="controls">${showSeg(() => { hideTip(); draw(); })}</div>
      ${legend()}
      <div id="board-rows">${boardRows(b, all)}</div>
      ${nEst === 0 ? html`<p class="muted" style="margin-top:1rem">No estimates for this benchmark: no same-capability benchmark calibrates it well enough (see <a href="/calibration">Calibration</a>).</p>` : nothing}`;
}
const host = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };

// a leaderboard longer than TAIL.min_rows folds where the scores drop below TAIL.below (never
// before row TAIL.min_rows), if more than TAIL.more_below rows score below it; one longer than
// TAIL.long_rows shows at most its top TAIL.long_share. The first hidden rows fade out above a
// "Show N more" button
const TAIL = { min_rows: 25, below: 0.15, more_below: 5, long_rows: 120, long_share: 1 / 3 };
let tailOpen = null;   // the benchmark whose folded tail is open
// [index of the first folded row, whether the TAIL.below score set it], or null: no fold
function tailCut(rows) {
  if (rows.length <= TAIL.min_rows) return null;
  const low = rows.findIndex((s) => s.v < TAIL.below);
  const byLow = low >= 0 && rows.length - low > TAIL.more_below;   // rows are sorted: all from low on are below
  let cut = byLow ? Math.max(low, TAIL.min_rows) : rows.length, byScore = byLow;
  if (rows.length > TAIL.long_rows && Math.ceil(rows.length * TAIL.long_share) < cut) {
    cut = Math.ceil(rows.length * TAIL.long_share);
    byScore = false;
  }
  return cut < rows.length ? [cut, byScore] : null;
}

function boardRows(b, scores) {
  const rows = scores.filter(visible);   // highest first (data/b/...)
  const hi = Math.max(0.1, ...rows.map((s) => s.v + (s.s === "e" ? s.sd : 0)));
  const { max: axisMax, ticks } = axis(hi);
  const X = (v) => Math.max(0, Math.min(100, (v / axisMax) * 100));

  // the rows keep their elements by model, so a filter or another benchmark only moves and updates them,
  // and are drawn again only for other scores or another filter (not when the tail folds)
  const rowsOf = (part, from) => guard([scores, prefs.show, from], () => repeat(part, (s) => s.m, (s, i) => {
    const m = ix.model.get(s.m);
    const est = s.s === "e";
    const rank = est ? `≈${from + i + 1}` : String(from + i + 1);
    // (one line, and the dot and link written out rather than dot() and modelLink(): a leaderboard
    // has hundreds of rows, and every node and nested template in a row is paid for in each)
    return html`<div class="row ${est ? "e " + s.tier : "m"}" data-p="${m.provider}"><div class="rank ${est ? "est" : ""}">${rank}</div><div class="who"><span class="dot" data-p="${m.provider}" aria-hidden="true"></span><a href="${modelHref(m)}">${m.name}</a>${tierFlag(s)}</div><div class="track" data-tip="${s.m}:${s.b}" tabindex="0" aria-label="${m.name}: ${est ? "estimated " : ""}${pct(s.v)} percent">${track(s, X, ticks)}</div><div class="val">${est ? "≈" : ""}${pct(s.v)}%${est ? html`<span class="pm">±${pct(s.sd)}</span>` : nothing}</div></div>`;
  }));
  const tail = tailCut(rows);
  const open = tailOpen === b.id;
  let body;
  if (!rows.length) body = html`<p class="empty">No scores to show with the current filter.</p>`;
  else if (!tail) body = rowsOf(rows, 0);
  else {
    const [cut, byScore] = tail;
    const more = `Show ${rows.length - cut} more${byScore ? ` · scoring below ${Math.round(TAIL.below * 100)}%` : ""}`;
    body = html`${rowsOf(rows.slice(0, cut), 0)}<div class="board-tail${open ? "" : " folded"}" id="board-tail">${rowsOf(rows.slice(cut), cut)}</div>
          <div class="tail-ctl"><button type="button" class="btn tail-btn" aria-controls="board-tail" aria-expanded="${open}"
            data-more="${more}" @click=${toggleTail}>${open ? "Show fewer" : more}</button></div>`;
  }
  return html`
      <div class="board" role="list">
        <div class="axis" aria-hidden="true"><span></span><span></span>
          <div class="ticks">${ticks.map((t) => html`<span style="left:${X(t)}%">${Math.round(t * 100)}</span>`)}</div><span></span></div>
        ${body}
      </div>`;
}
// unfolds (or folds back) the tail, easing its height between the faded peek and all its rows
function toggleTail(e) {
  const btn = e.currentTarget, tail = $("#board-tail");
  const open = tailOpen !== board.b.id;
  const from = tail.offsetHeight;
  tailOpen = open ? board.b.id : null;
  draw();
  const to = tail.offsetHeight;
  if (!open) btn.scrollIntoView({ block: "nearest" });
  if (!reduceMotion.matches) ease(tail, [{ height: from + "px" }, { height: to + "px" }], open ? 420 : 300);
}
