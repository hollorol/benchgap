/* The tooltip on every score (and on any element with data-tiptext): hovered or focused on a
 * desktop, a sheet from the bottom on touch screens. */
import { html, render, nothing } from "./vendor/lit-html.js";
import { $, ix, pct, load, scoreUrl, remember, methodLabel, benchLabel, benchHref, mappingHref } from "./core.js";

const tip = $("#tip");

// "Benchmark = x%" for each measured input of an estimate
export const sourceList = (s, link) =>
  s.via.from.map((f, i) => {
    const label = benchLabel(f.b);
    const fb = ix.bench.get(f.b);
    return html`${i ? ", " : ""}${link && fb ? html`<a href="${benchHref(fb)}">${label}</a>` : label} = ${f.v == null ? "?" : pct(f.v) + "%"}`;
  });

// the model's name, heading the sheet (the hover tooltip sits next to it)
const modelLine = (s) => html`<div class="h3">${ix.model.get(s.m).name}</div>`;
// the calibration an estimate came from, if it came from a single one
export const fitHref = (s) => (s.s === "e" && s.via && s.via.kind === "uni" ? mappingHref(s.via.mapping) : "");

function describeEstimate(s, link) {
  const b = ix.bench.get(s.b);
  const head = html`<div class="t-h">${b.label}<span class="t-tier ${s.tier}">${s.tier} confidence</span></div>${link ? modelLine(s) : nothing}`;
  if (s.partial) {   // a matrix cell: the details are loading (details())
    return html`${head}<div class="t-v"><i>≈ ${pct(s.v)}%</i></div><div class="t-note">Loading where it came from…</div>`;
  }
  const why = s.why && s.why.length
    ? html`<ul>${s.why.map((w) => html`<li>${w}</li>`)}</ul>`
    : html`<div class="t-note">Low cross-validated error, inside the fitted range.</div>`;
  return html`${head}<div class="t-v"><i>≈ ${pct(s.v)}%</i> <span class="t-note">± ${pct(s.sd)} pp</span></div><div>Estimated from ${sourceList(s, link)} via ${methodLabel(s.method)}, fitted on ${s.via.n} models measured on both.</div>${why}<div class="t-note">Not a measured score.</div>`;
}
const HARNESSES = { "artificial-analysis": "Artificial Analysis", "vals-ai": "Vals AI" };
const harnessNote = (b) => HARNESSES[b.harness] ? `Run by ${HARNESSES[b.harness]}.` : "A published result.";
function describeMeasured(s, link) {
  const b = ix.bench.get(s.b);
  return html`<div class="t-h">${b.label} · measured</div>${link ? modelLine(s) : nothing}<div class="t-v">${pct(s.v)}%</div><div class="t-note">${harnessNote(b)}</div>`;
}

let tipEl = null;        // element the tooltip currently describes
let tipKey = null;       // the score it describes (its data-tip), if any
let tipSize = null;      // its measured size, so moves don't re-measure
let tipAt = [0, 0];      // where it points
function showTip(el, content, x, y) {
  if (el !== tipEl) {
    render(content, tip);
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tipSize = [r.width, r.height];
    tipEl = el;
    tipKey = el.getAttribute("data-tip");
  }
  moveTip(x, y);
}
function moveTip(x, y) {
  tipAt = [x, y];
  const [w, h] = tipSize;
  let left = x + 14, top = y + 14;
  if (left + w > window.innerWidth - 8) left = Math.max(8, x - w - 14);
  if (top + h > window.innerHeight - 8) top = Math.max(8, y - h - 14);
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
let scrim = null;        // the dimmed backdrop behind a sheet
export function hideTip() {
  tip.hidden = true;
  tipEl = null;
  tip.classList.remove("sheet");
  tip.setAttribute("role", "tooltip");
  if (scrim) scrim.hidden = true;
}
// after a redraw: hides the tooltip if its element is gone or now shows another score
export function hideStaleTip() {
  if (tipEl && (!tipEl.isConnected || tipEl.getAttribute("data-tip") !== tipKey)) hideTip();
}
const tipTarget = (e) => e.target.closest("[data-tip],[data-tiptext]");
// the details for el; link: with links (in a sheet, which can be tapped)
function tipFor(el, link) {
  const key = el.getAttribute("data-tip");
  if (key) {
    const s = ix.cell.get(key);
    if (s && s.partial) details(s, el);
    if (s) return s.s === "e" ? describeEstimate(s, link) : describeMeasured(s, link);
  }
  return el.getAttribute("data-tiptext") || null;
}
// a matrix cell's estimate loads where it came from when its tooltip opens, then redraws the tooltip
function details(s, el) {
  load(scoreUrl(s)).then((full) => {
    remember([full]);
    if (tipEl !== el || tip.hidden) return;
    if (tip.classList.contains("sheet")) openSheet(el);
    else { tipEl = null; showTip(el, tipFor(el, false), ...tipAt); }
  }, () => {});
}
// touch screens cannot hover: a tap opens the details as a sheet instead
const touch = matchMedia("(pointer: coarse)");   // as the CSS
function openSheet(el) {
  const content = tipFor(el, true);
  if (!content) return;
  const s = ix.cell.get(el.getAttribute("data-tip"));
  const fit = s && fitHref(s);
  if (!scrim) {
    scrim = document.createElement("div");
    scrim.className = "tip-scrim";
    scrim.addEventListener("click", hideTip);
    document.body.append(scrim);
  }
  scrim.hidden = false;
  render(html`${content}<div class="tip-actions">${fit ? html`<a href="${fit}">See the calibration</a>` : ""}<button type="button" class="btn" @click=${hideTip}>Close</button></div>`, tip);
  tip.classList.add("sheet");
  tip.setAttribute("role", "dialog");
  tip.style.left = tip.style.top = "";
  tip.hidden = false;
  tipEl = el;
  tipKey = el.getAttribute("data-tip");
}
document.addEventListener("click", (e) => {
  if (!touch.matches || tip.contains(e.target)) return;
  const el = tipTarget(e);
  if (el && !el.closest("a")) openSheet(el);   // a link inside a target (calibration cells) just navigates
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !tip.hidden) hideTip(); });
document.addEventListener("mouseover", (e) => {
  if (touch.matches) return;   // the tap's emulated mouse events; the click opens a sheet
  const el = tipTarget(e);
  const content = el && (el === tipEl || tipFor(el));
  if (!content) return hideTip();
  showTip(el, content, e.clientX, e.clientY);
});
document.addEventListener("mousemove", (e) => {
  if (tipEl && tipTarget(e) === tipEl) moveTip(e.clientX, e.clientY);
});
document.addEventListener("focusin", (e) => {
  if (touch.matches || tip.classList.contains("sheet")) return;
  const el = tipTarget(e);
  const content = el && tipFor(el);
  if (!content) return hideTip();
  const r = el.getBoundingClientRect();
  showTip(el, content, r.left + r.width / 2, r.bottom);
});
document.addEventListener("focusout", () => { if (!tip.classList.contains("sheet")) hideTip(); });
window.addEventListener("scroll", hideTip, { passive: true });
