/* The pieces several pages are made of: fragments (templates), controls, animations, axes and colours. */
import { html, svg, render, nothing, ref, guard, repeat } from "./vendor/lit-html.js";
import { store, prefs, byCapability, capLabel, modelHref, setMeta, show } from "./core.js";
import { hideTip } from "./tip.js";

// --- shared fragments -------------------------------------------------------
export const dot = (m) => html`<span class="dot" data-p="${m.provider}" aria-hidden="true"></span>`;
export const modelLink = (m) => html`<a href="${modelHref(m)}">${m.name}</a>`;
export const confidenceFlag = (tier) => html`<span class="flag ${tier}">${tier} confidence</span>`;
const COPY_ICON = html`<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 3.5v-.5A1.5 1.5 0 0 0 9 1.5H3A1.5 1.5 0 0 0 1.5 3v6A1.5 1.5 0 0 0 3 10.5h.5"/></svg><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7"/></svg>`;
// a page's header: eyebrow, h1 and lede, each text or a template (as Pages::header)
export const pageHead = (eyebrow, h1, lede) => html`<header class="page-head reveal"><div class="eyebrow">${eyebrow}</div><h1 class="h2">${h1}</h1>${lede ? html`<p class="lede">${lede}</p>` : nothing}</header>`;

// copy text; on success flag the button with .done for a moment, otherwise select `fallback` for a manual copy
async function copyText(text, button, fallback) {
  try {
    await navigator.clipboard.writeText(text);
    button.classList.add("done");
    setTimeout(() => button.classList.remove("done"), 1500);
  } catch (err) {
    getSelection().selectAllChildren(fallback);
  }
}
// a copy button's click: copies the text of the element its data-copy names
function copyById(e) {
  const button = e.currentTarget, el = document.getElementById(button.dataset.copy);
  copyText(el.textContent, button, el);
}
// a button copying the text of the element with id
export const copyButton = (id, label = "Copy") => html`<button type="button" class="code-copy" data-copy="${id}" aria-label="${label}" title="Copy" @click=${copyById}>${COPY_ICON}</button>`;
// <pre> with a copy button pinned to its top-right corner (it stays put while the block scrolls)
export const codeBox = (id, cls, inner) => html`<div class="code-box"><pre class="code ${cls}" id="${id}">${inner}</pre>${copyButton(id)}</div>`;

export const legend = () => html`<div class="legend" aria-label="Legend">
      <span><span class="sw measured"></span>Measured score</span>
      <span><span class="sw high"></span><i>High confidence</i></span>
      <span><span class="sw medium"></span><i>Medium confidence</i></span>
      <span><span class="sw low"></span><i>Low confidence</i>: treat with caution</span>
      <span><span class="whisk"></span>± cross-validated error</span>
    </div>`;

// segmented control with a sliding pill; options are [value, label] pairs. onPick(value) redraws
// the page with the value current, and the pill slides to it
const placed = new WeakSet();
function placeThumb(seg) {
  const on = seg.querySelector('[aria-pressed="true"]');
  if (!on) return;
  seg.style.setProperty("--x", on.offsetLeft + "px");
  seg.style.setProperty("--w", on.offsetWidth + "px");
}
// a new control: placed as soon as it has a size (and again when webfonts load), sliding from then on;
// placed again whenever its option changes, so a page drawn after its route loads moves it too
function initSeg(seg) {
  if (!seg || placed.has(seg)) return;
  placed.add(seg);
  new ResizeObserver(() => placeThumb(seg)).observe(seg);
  new MutationObserver(() => placeThumb(seg)).observe(seg, { subtree: true, attributeFilter: ["aria-pressed"] });
  requestAnimationFrame(() => requestAnimationFrame(() => seg.classList.remove("instant")));
}
export const seg = (ariaLabel, options, current, onPick) =>
  html`<span class="seg instant" role="group" aria-label="${ariaLabel}" ${ref(initSeg)}><span class="seg-thumb" aria-hidden="true"></span>${options
    .map(([k, l]) => html`<button type="button" data-seg="${k}" aria-pressed="${k === current}" @click=${(e) => {
      if (k === current) return;
      const control = e.currentTarget.parentNode;
      onPick(k);
      placeThumb(control);
    }}>${l}</button>`)}</span>`;
// option labels: long, and short for phones
const SHOW_OPTIONS = [["measured", "Measured only", "Measured"], ["reliable", "+ reliable estimates", "+ Reliable est."], ["all", "+ all estimates", "+ All est."]]
  .map(([k, long, short]) => [k, html`<span class="lg">${long}</span><span class="sh">${short}</span>`]);
// which scores to show; redraw() shows the page with the new choice
export const showSeg = (redraw) => html`<span class="show-ctl"><span class="ctl-label">Show</span>${seg("Which scores to show", SHOW_OPTIONS, prefs.show, (value) => {
  prefs.show = value;
  store.set("show", prefs.show);
  redraw();
})}</span>`;

export const tierFlag = (s) => (s.s === "e" && s.tier === "low" ? html`<span class="flag low" title="Low-confidence estimate"><span class="lg">⚠ low conf.</span><span class="sh">⚠ low</span></span>` : nothing);

// the grid lines at ticks (fractions, placed by X), the same in every row of a chart: built once,
// then copied into each row (natively, far cheaper than a template per line in every row)
const grids = new Map();
function gridLines(ticks, X) {
  const at = ticks.map((t) => X(t)), key = at.join(" ");
  if (!grids.has(key)) {
    const lines = document.createElement("template");
    lines.innerHTML = at.map((x) => `<span class="grid" style="left:${x}%"></span>`).join("");   // numbers only
    grids.set(key, lines.content);
  }
  return guard([key], () => grids.get(key).cloneNode(true));
}
// bar track: grid lines, the bar, and a ±sd whisker for estimates; X maps a fraction to %
export function track(s, X, ticks) {
  const e = s.s === "e";
  const whisker = e
    ? html`<span class="whisker" style="left:${X(Math.max(0, s.v - s.sd))}%;width:${X(Math.min(1, s.v + s.sd)) - X(Math.max(0, s.v - s.sd))}%"></span>`
    : nothing;
  return html`${gridLines(ticks, X)}<span class="bar ${e ? "e " + s.tier : "m"}" style="width:${X(s.v)}%"></span>${whisker}`;
}

// a 0..max axis for values up to hi, max rounded up to a tenth, with its ticks
export function axis(hi, lo = 0) {
  const min = Math.max(0, Math.floor(lo * 10) / 10), max = Math.min(1, Math.ceil(hi * 10) / 10);
  const step = max - min > 0.5 ? 0.1 : max - min > 0.2 ? 0.05 : 0.02, ticks = [];
  for (let v = min; v <= max + 1e-9; v += step) ticks.push(v);
  return { min, max, ticks };
}

// a square scatter of points ([x, y, tooltip]) on one scale zoomed to them, with the y = x line
export function diagScatter(points, xLabel, yLabel, ariaLabel) {
  const W = 300, H = 300, L = 40, R = 10, T = 10, B = 38;
  const vals = points.flatMap(([x, y]) => [x, y]);
  const { min: lo, max: hi, ticks } = axis(Math.max(...vals) + 0.02, Math.min(...vals) - 0.02);
  const clamp = (v) => Math.min(hi, Math.max(lo, v));
  const px = (v) => L + ((clamp(v) - lo) / (hi - lo)) * (W - L - R), py = (v) => H - B - ((clamp(v) - lo) / (hi - lo)) * (H - T - B);
  const grid = ticks.map((v) => svg`<line class="gl" x1="${px(v)}" x2="${px(v)}" y1="${T}" y2="${H - B}"/><text x="${px(v)}" y="${H - B + 16}" text-anchor="middle">${Math.round(v * 100)}</text><line class="gl" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/><text x="${L - 6}" y="${py(v) + 4}" text-anchor="end">${Math.round(v * 100)}</text>`);
  const dots = points.map(([x, y, tip]) => svg`<circle class="pt" cx="${px(x).toFixed(1)}" cy="${py(y).toFixed(1)}" data-tiptext="${tip}"/>`);
  return html`<svg class="plot mv-plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="${ariaLabel}">
        ${grid}<line class="ax" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><line class="ax" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
        <line class="ideal" x1="${px(lo)}" y1="${py(lo)}" x2="${px(hi)}" y2="${py(hi)}"/>${dots}
        <text class="lbl" x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle">${xLabel}</text>
        <text class="lbl" transform="translate(12 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">${yLabel}</text></svg>`;
}

// --- animation --------------------------------------------------------------
export const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const EASE = "cubic-bezier(.2, .7, .2, 1)";
// animates el between keyframes, clipped (.animating) meanwhile so a changing height hides overflow
export function ease(el, frames, ms, done) {
  el.classList.add("animating");
  const a = el.animate(frames, { duration: ms, easing: EASE });
  a.onfinish = () => { el.classList.remove("animating"); if (done) done(); };
  return a;
}
// shows or hides a list: it grows from (or shrinks to) nothing, so the rows below slide instead of jumping
const BOX = ["height", "paddingTop", "paddingBottom", "marginTop", "marginBottom"];
export function toggleList(list, open) {
  if (list.anim) list.anim.finish();  // a click mid-animation: settle the last one first
  if (open === !list.hidden) return;
  if (reduceMotion.matches) { list.hidden = !open; return; }
  list.hidden = false;
  const cs = getComputedStyle(list);
  const full = { opacity: 1, transform: "none" };
  BOX.forEach((k) => { full[k] = cs[k]; });
  const none = { opacity: 0, transform: "translateY(-4px)" };
  BOX.forEach((k) => { none[k] = "0px"; });
  list.anim = ease(list, open ? [none, full] : [full, none], open ? 240 : 180, () => {
    list.anim = null;
    if (!open) list.hidden = true;
  });
}
// draws a template into el (a part of the page drawn on its own, empty in the page's template):
// the first time at once; then the old content fades out, and the new fades in while el eases
// to its new height
export function swap(el, template) {
  const id = (el.swapId = (el.swapId || 0) + 1);  // a newer swap supersedes this one
  if (!el.hasChildNodes() || reduceMotion.matches) { render(template, el); return; }
  el.getAnimations().forEach((a) => a.cancel());
  const from = el.offsetHeight;
  const out = el.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(4px)" }],
    { duration: 130, easing: "ease-in", fill: "forwards" });
  out.onfinish = () => {
    if (id !== el.swapId) return;
    render(template, el);
    out.cancel();
    const to = el.offsetHeight;
    ease(el, [{ opacity: 0, transform: "translateY(6px)", height: from + "px" }, { opacity: 1, transform: "none", height: to + "px" }], 280);
  };
}
// tpl in a new element whenever key changes, so it plays its entrance (.fresh) for the new value
export const fresh = (key, tpl) => repeat([key], (k) => k, () => tpl);

// redraws with update() and slides the items (selector, each with its data-key) in root from where
// they were to where the redraw put them; the new ones fade in, one after another. Only the items in
// view are measured and moved: a leaderboard has hundreds of rows, and a row from out of view (or to
// it) would only streak across the page
export function flip(root, selector, update) {
  if (!root || reduceMotion.matches) return update();
  const vh = innerHeight, from = new Map();
  // the items in view in document order, each with its box (a moving one where it is shown now)
  const inView = (each) => {
    for (const el of root.querySelectorAll(selector)) {
      const r = el.getBoundingClientRect();
      if (r.top >= vh) break;
      if (r.bottom > 0) each(el, r);
    }
  };
  inView((el, r) => from.set(el.dataset.key, r.top));
  update();
  // the last redraw's moves still running: their items are measured where the new redraw put them
  (root.flips || []).forEach((a) => a.cancel());
  root.flips = [];
  let entering = 0;
  inView((el, r) => {
    const top = from.get(el.dataset.key);
    if (top === undefined || Math.abs(top - r.top) > vh) {
      root.flips.push(el.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }],
        { duration: 320, delay: 90 + Math.min(entering++, 18) * 16, easing: EASE, fill: "backwards" }));
    } else if (Math.abs(top - r.top) > 0.5) {
      root.flips.push(el.animate([{ transform: `translateY(${top - r.top}px)` }, { transform: "none" }], { duration: 460, easing: EASE }));
    }
  });
}

// scrolls a row of pills (phones only; the site nav up to tablets, as the CSS) so its current one is in the middle
export const phone = matchMedia("(max-width: 760px)"), pillNav = matchMedia("(max-width: 1040px)");
export function centerIn(row, selector, when = phone) {
  const chip = when.matches && row && row.querySelector(selector);
  if (chip) row.scrollLeft += chip.getBoundingClientRect().left - row.getBoundingClientRect().left - (row.clientWidth - chip.offsetWidth) / 2;
}

// --- calibration colours ------------------------------------------------------
// green -> amber -> red over 0..gate pp of LOO error (same stops as the legend ramp)
const LOSS_STOPS = [[0, [16, 185, 129]], [0.5, [245, 158, 11]], [1, [239, 68, 68]]];
export const LOSS_RAMP = `linear-gradient(90deg, ${LOSS_STOPS.map(([t, c]) => `rgb(${c.join(",")}) ${t * 100}%`).join(", ")})`;
export function lossColor(lossPP, gate) {
  const t = Math.max(0, Math.min(lossPP / gate, 1));
  const i = Math.max(1, LOSS_STOPS.findIndex(([t1]) => t <= t1));
  const [t0, c0] = LOSS_STOPS[i - 1], [t1, c1] = LOSS_STOPS[i];
  const u = (t - t0) / (t1 - t0);
  return `rgb(${c0.map((a, k) => Math.round(a + (c1[k] - a) * u)).join(",")})`;
}

// a row of capability chips over items, All first: byCap (items by capability id, and
// "all"), caps ({ id, label, n }) and chips(current, onPick), onPick(id) redrawing with the clicked one current
export function capFilter(items, capOfItem) {
  const groups = byCapability(items, capOfItem);
  const byCap = new Map([...groups, ["all", items]]);
  const caps = [{ id: "all", label: "All" }, ...groups.map(([id]) => ({ id, label: capLabel(id) }))].map((c) => ({ ...c, n: byCap.get(c.id).length }));
  const chips = (current, onPick) => html`<div class="pm-caps chip-row" role="group" aria-label="Capability">${caps
    .map((c) => html`<button type="button" class="chip" data-cap="${c.id}" aria-pressed="${c.id === current}" @click=${() => { hideTip(); onPick(c.id); }}>${c.label}<span class="cnt">${c.n}</span></button>`)}</div>`;
  return { byCap, caps, chips };
}

// --- not found ----------------------------------------------------------------
// the 404 page (as Pages::notFound); msg says what was not found
export function renderNotFound(msg) {
  setMeta("Not found", "", null);
  show(html`<div class="page"><section class="nf reveal">
      <svg class="nf-mark" viewBox="0 0 120 64" aria-hidden="true"><defs><pattern id="nf-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="5"/></pattern></defs>
        <rect x="4" y="20" width="26" height="44" rx="2"/><rect class="nf-gap" x="47" y="4" width="26" height="60" rx="2" fill="url(#nf-hatch)"/><rect x="90" y="30" width="26" height="34" rx="2"/></svg>
      <div class="eyebrow">Not found · 404</div>
      <h1 class="display">This one is a gap we <em>can’t</em> fill.</h1>
      <p class="lede">${msg} It may have been renamed, or the link has a typo.</p>
      <a class="btn nf-home" href="/">Back to the leaderboard</a>
      <nav class="nf-links" aria-label="Elsewhere on benchgap"><div class="ctl-label">Or try</div>
        <a href="/matrix">Every model × every benchmark</a><a href="/calibration">Which benchmarks predict which</a><a href="/api">Every score in the public API</a></nav>
    </section></div>`);
}

// the page shown when the site's data (or a page's) could not be loaded
export function renderError(err) {
  show(html`<div class="page">${pageHead("Error", "The score database could not be loaded.", `${err.message}. Please try again in a moment.`)}</div>`);
}
