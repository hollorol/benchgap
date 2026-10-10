/* Two models head to head: who leads where both were measured, then every benchmark by capability. */
import { html, nothing, repeat, render, ref } from "../vendor/lit-html.js";
import { D, ix, $, pct, prefs, load, modelUrl, remember, visible, benchHref, setMeta, show, go } from "../core.js";
import { hideTip } from "../tip.js";
import { fold, ranked, mark } from "../search.js";
import { dot, pageHead, legend, seg, showSeg, fresh, renderNotFound } from "../ui.js";

// the page's lede (Pages::COMPARE_INTRO)
const LEDE = "Measured scores decide who leads; estimates fill in the rest, hatched, with their confidence. "
  + "Pick two models, or one and the frontier model closest to it in general performance.";
const GENERAL_TIP = "General performance: the mean percentile of a model's measured scores across the listed benchmarks. "
  + "The frontier is its strongest tenth. Measured scores only.";
const cmpHref = (a, b, from) =>
  `/compare/${encodeURIComponent(a)}${b ? "/" + encodeURIComponent(b) : (from && from !== "any" ? "/from/" + encodeURIComponent(from) : "")}`;
const providerName = (p) => D.meta.providers[p] || p;

let page = null;   // the open pair: its models, how B was chosen, the standings and both models' scores
                  // (and .prev, the pair shown, dimmed, until they are in)
let open = null;   // the capabilities opened (ids); null: the one with the most benchmarks in common

// data: every model's general performance and the frontier (data/compare.json, Compare.php):
// general is the mean percentile of a model's measured scores across the listed benchmarks,
// the frontier is its strongest tenth. Two models (/compare/<a>/<b>), or one model (/compare/<a>)
// and the frontier model closest to it - of one provider (/compare/<a>/from/<p>), or of any - a
// pick that follows the field. Returns true when the page was already open (only the pair changed).
export function renderCompare(data) {
  const parts = location.pathname.split("/").slice(2).filter(Boolean).map(decodeURIComponent);
  const general = new Map(data.compare.general.map(([id, g, n]) => [id, { g, n }]));   // by model id
  const rankOf = new Map(data.compare.general.map(([id], i) => [id, i + 1]));          // strongest first
  const frontier = data.compare.frontier.filter(([id]) => ix.model.has(id));          // strongest first
  // the frontier model closest in general performance to m (never m itself), of one provider or
  // of any; a provider with no frontier model of its own falls back to the whole frontier
  const closestFrontier = (m, provider) => {
    const pool = provider && provider !== "any" ? frontier.filter((f) => ix.model.get(f[0]).provider === provider) : frontier;
    const t = general.get(m.id);
    let best = null, bestD = Infinity;
    for (const f of pool) {
      if (f[0] === m.id) continue;
      if (t && Math.abs(f[1] - t.g) < bestD) { bestD = Math.abs(f[1] - t.g); best = f; }
      if (best === null) best = f;   // m has no standing: the strongest of the pool
    }
    return best ? ix.model.get(best[0]) : (pool !== frontier ? closestFrontier(m, "any") : null);
  };
  const fromParts = parts[1] === "from" ? (parts[2] || "any") : null;
  const auto = parts.length < 2 || fromParts !== null;   // /compare, /compare/<a> and /compare/<a>/from/<p>
  const from = auto ? (fromParts || "any") : null;
  if (from && from !== "any" && !D.meta.providers[from]) return renderNotFound(`No provider “${from}”.`);
  let A = parts[0] ? ix.modelBySlug.get(parts[0]) : ix.model.get(frontier[0]?.[0] ?? -1);
  if (parts[0] && !A) return renderNotFound(`No model “${parts[0]}”.`);
  let B = !auto && parts[1] ? ix.modelBySlug.get(parts[1]) : null;
  if (!auto && parts[1] && !B) return renderNotFound(`No model “${parts[1]}”.`);
  if (A && B && A.id === B.id) return renderNotFound("Pick two different models to compare.");
  if (!A) A = ix.listedModels[0] || null;
  if (!B && auto) B = A && closestFrontier(A, from);
  if (!A || !B) return renderNotFound("Nothing to compare: no models with scores.");
  setMeta(parts.length ? `${A.name} vs ${B.name} benchmark scores` : "Compare two LLM models",
    `Compare two LLM models benchmark by benchmark: ${A.name} vs ${B.name}, measured scores and estimates side by side.`,
    parts.length ? null : "/compare");

  const inPlace = !!$("#cmp-page");
  picking = null;
  // the providers whose frontier model can fill B: those with one, by name
  const frontierProviders = [...new Set(frontier.map(([id]) => ix.model.get(id).provider))]
    .sort((x, y) => providerName(x).localeCompare(providerName(y)));
  const prev = page && (page.sa ? page : page.prev);
  page = { prev: inPlace ? prev : null, A, B, auto, from, general, rankOf, nStanding: data.compare.general.length, isFrontier: new Set(frontier.map(([id]) => id)), frontierProviders, sa: null, sb: null, failed: false };
  draw();

  // both models' scores load in (cached by the model pages' own visits); the body draws when they are in
  const at = page;
  Promise.all([load(modelUrl(A.slug)), load(modelUrl(B.slug))]).then(([da, db]) => {
    if (page !== at) return;   // another pair was opened meanwhile
    at.sa = remember(da.scores);
    at.sb = remember(db.scores);
    draw();
  }, () => {
    if (page !== at) return;
    at.failed = true;
    draw();
  });
  return inPlace;
}

function draw() {
  const p = page;
  show(html`<div class="page" id="cmp-page">
      ${pageHead("Model compare", "Two models, head to head", LEDE)}
      <section class="cmp-pair" aria-label="The two models">
        ${slot(p, "A")}
        <div class="cmp-vs"><span>vs</span><button type="button" class="cmp-swap" aria-label="Swap the two models" title="Swap" @click=${() => go(cmpHref(p.B.slug, p.A.slug))}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h12l-3-3"/><path d="M17 17H5l3 3"/></svg></button></div>
        ${slot(p, "B")}
      </section>
      ${p.failed ? html`<p class="muted">The models’ scores could not be loaded. Please try again.</p>`
        : html`<div class="cmp-body" aria-busy="${!p.sa}">${p.sa ? body(p) : p.prev ? body(p.prev) : nothing}</div>`}
    </div>`);
}

// --- the model pickers: a box to type in over the models, filtered as you type ------------
let picking = null;   // the open picker ("A" or "B"), its results and the one the arrow keys are on
let models = null;    // the listed models as search items (search.js ranked)
const MAX_SHOWN = 60;
const pickerItems = () => (models ??= ix.listedModels.map((m) => ({
  m, label: m.name, weight: m.n_measured, text: fold(`${m.name} ${m.slug} ${providerName(m.provider)}`),
})));
// where picking slug for model `which` leads: the other side's model swaps the two; a new A keeps
// how B is chosen, a new B pins the pair
function pickHref(which, slug) {
  const { A, B, auto, from } = page;
  if (slug === (which === "A" ? B : A).slug) return cmpHref(B.slug, A.slug);
  return which === "A" ? cmpHref(slug, auto ? null : B.slug, auto ? from : null) : cmpHref(A.slug, slug);
}
function openPicker(which) {
  picking = { which, shown: [], active: -1, input: null, list: null };
  hideTip();
  draw();
}
function closePicker(focusFace) {
  if (!picking) return;
  const which = picking.which;
  picking = null;
  draw();
  if (focusFace) $(`#cmp-face-${which}`)?.focus();
}
// the panel's results for what is typed (nothing typed: the most measured models)
function drawResults() {
  const { which, input, list } = picking;
  const q = input.value.trim();
  const pool = q ? ranked(pickerItems(), q) : pickerItems().slice().sort((a, b) => b.weight - a.weight);
  const current = (which === "A" ? page.A : page.B).id, other = (which === "A" ? page.B : page.A).id;
  picking.shown = pool.slice(0, MAX_SHOWN);
  render(html`<li class="srch-group" role="presentation">${q ? "Models" : "Most measured"}<span>${picking.shown.length < pool.length ? `${picking.shown.length} of ${pool.length}` : q ? pool.length : ""}</span></li>
    ${picking.shown.map(({ m }, i) => html`<li role="option" id="cmp-opt-${i}" aria-selected="false"><a href="${pickHref(which, m.slug)}" tabindex="-1" data-i="${i}">${dot(m)}<span class="srch-name">${q ? mark(m.name, q) : m.name}</span><span class="srch-meta">${providerName(m.provider)} · ${m.n_measured} measured${
      m.id === current ? " · picked" : m.id === other ? ` · model ${which === "A" ? "B" : "A"}: swaps them` : ""}</span></a></li>`)}
    ${pool.length ? nothing : html`<li class="srch-empty" role="presentation">No model matches “${q}”.</li>`}`, list);
  setActive(q && pool.length ? 0 : -1);
}
function setActive(i) {
  const { input, list } = picking;
  list.querySelector('[aria-selected="true"]')?.setAttribute("aria-selected", "false");
  picking.active = i;
  const li = i >= 0 && list.querySelector("#cmp-opt-" + i);
  if (li) { li.setAttribute("aria-selected", "true"); li.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", li.id); }
  else input.removeAttribute("aria-activedescendant");
}
// the panel, once drawn: its box takes the focus and the results come in
function panelIn(el) {
  if (!el || !picking || picking.input) return;
  picking.input = el.querySelector("input");
  picking.list = el.querySelector("ul");
  drawResults();
  // (once the render is done: the panel is not in the document yet)
  queueMicrotask(() => picking?.input?.focus({ preventScroll: true }));
}
function pickerKeys(e) {
  const { shown, active } = picking;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (shown.length) setActive((active + (e.key === "ArrowDown" ? 1 : shown.length - 1 + (active < 0 ? 1 : 0))) % shown.length);
  } else if (e.key === "Enter") {
    e.preventDefault();
    const it = shown[active >= 0 ? active : 0];
    if (it) go(pickHref(picking.which, it.m.slug));
  } else if (e.key === "Escape") {
    e.preventDefault();
    closePicker(true);
  }
}
// the panel closes when the focus leaves it (a click outside, Tab), but not for its own face:
// that click closes it
function pickerLeft(e) {
  const to = e.relatedTarget;
  if (to && (e.currentTarget.contains(to) || to.id === `cmp-face-${picking?.which}`)) return;
  closePicker(false);
}
const picker = (which) => html`<div class="search-pop cmp-pop" ${ref(panelIn)} @focusout=${pickerLeft}>
    <input class="cmp-q" type="search" role="combobox" aria-label="Find model ${which}" aria-expanded="true" aria-controls="cmp-results"
      aria-autocomplete="list" placeholder="Type a model or provider…" autocomplete="off" spellcheck="false"
      @input=${drawResults} @keydown=${pickerKeys}>
    <ul class="search-results" id="cmp-results" role="listbox" aria-label="Models"
      @mousedown=${(e) => e.preventDefault()}
      @mousemove=${(e) => { const a = e.target.closest("a[data-i]"); if (a && Number(a.dataset.i) !== picking.active) setActive(Number(a.dataset.i)); }}></ul>
    <p class="search-keys"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> pick · <kbd>Esc</kbd> close</p>
  </div>`;

// one model's card: its picker (a native select laid over the card's face) and its standing; B's
// also says how it was chosen
function slot(p, which) {
  const { A, B, auto, from } = p;
  const m = which === "A" ? A : B;
  const g = p.general.get(m.id);
  const sub = which === "B" && auto
    ? `Picked for you: the frontier model ${from !== "any" && B.provider === from ? `of ${providerName(from)} ` : ""}closest to ${A.name}`
    : `${providerName(m.provider)} · ${m.n_measured} measured · ${m.n_estimated} estimated`;
  return html`<div class="card cmp-slot">
      <div class="cmp-slot-head"><span class="ctl-label">Model ${which}</span>${which === "B"
        ? seg("How model B is chosen", [["frontier", "Closest frontier"], ["pick", "Pick a model"]], auto ? "frontier" : "pick",
          (k) => go(k === "frontier" ? cmpHref(A.slug, null, "any") : cmpHref(A.slug, B.slug)))
        : nothing}</div>
      <div class="cmp-picker">
        <button type="button" class="cmp-face" id="cmp-face-${which}" aria-haspopup="listbox" aria-expanded="${picking?.which === which}"
          aria-label="Model ${which}: ${m.name}. Change" @click=${() => (picking?.which === which ? closePicker(false) : openPicker(which))}>
          <span class="dot" data-p="${m.provider}" aria-hidden="true"></span>
          <span class="cmp-picked">${fresh(m.id, html`<span class="cmp-name fresh">${m.name}</span>`)}${fresh(sub, html`<span class="cmp-sub fresh">${sub}</span>`)}</span>
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>
        </button>
        ${picking?.which === which ? picker(which) : nothing}
      </div>
      <dl class="kv">
        <dt><span data-tiptext="${GENERAL_TIP}" tabindex="0">general ⓘ</span></dt>
        <dd>${fresh(m.id, html`<span class="fresh">${g ? pct(g.g) : "—"}${p.isFrontier.has(m.id) ? html` <span class="flag high">frontier</span>` : nothing}</span>`)}</dd>
        <dt>rank</dt><dd>${fresh(m.id, html`<span class="fresh">${g ? `#${p.rankOf.get(m.id)} of ${p.nStanding}` : "no measured standing"}</span>`)}</dd>
      </dl>
      ${which === "B" ? html`<div class="cmp-from-fold${auto ? "" : " closed"}" ?inert=${!auto}><div><div class="cmp-from">
          <label class="ctl-label" for="cmp-from">Frontier of</label>
          <select class="select" id="cmp-from" @change=${(e) => go(cmpHref(A.slug, null, e.target.value))}>
            <option value="any" .selected=${from === "any"}>Any provider</option>
            ${p.frontierProviders.map((pr) => html`<option value="${pr}" .selected=${from === pr}>${providerName(pr)}</option>`)}
          </select>
          <span class="muted">follows the field as scores change</span>
        </div></div></div>` : nothing}
    </div>`;
}

// the filter (the leaderboard's Show), the verdict, then every benchmark by capability
function body(p) {
  const { A, B } = p;
  const aBy = new Map(p.sa.map((s) => [s.b, s])), bBy = new Map(p.sb.map((s) => [s.b, s]));
  const pair = (b) => [b, aBy.get(b.id), bBy.get(b.id)];
  const measuredOnly = prefs.show === "measured";

  // every benchmark both have a score on (as the filter shows them): who leads, by how much; the
  // ones measured on both first, then those leaning on an estimate, the most confident first
  const TIER = { high: 1, medium: 2, low: 3 };
  const kind = (x, y) => (x.s === "m" && y.s === "m" ? 0 : Math.max(x.s === "e" ? TIER[x.tier] : 0, y.s === "e" ? TIER[y.tier] : 0));
  const shared = ix.benchesByCap.flatMap(({ benches }) => benches.map(pair))
    .filter(([, x, y]) => x && y && visible(x) && visible(y))
    .map((r) => [...r, kind(r[1], r[2])])
    .sort((a, b) => a[3] - b[3]);
  const tally = { up: 0, down: 0, mUp: 0, mDown: 0, eUp: 0, eDown: 0 };
  let dsum = 0;
  for (const [, x, y, k] of shared) {
    const side = x.v > y.v ? "Up" : x.v < y.v ? "Down" : null;
    if (side) { tally[side.toLowerCase()]++; tally[(k ? "e" : "m") + side]++; }
    dsum += Math.abs(x.v - y.v);
  }
  const n = shared.length, nm = shared.filter((r) => !r[3]).length;
  // the leader's count first (A's on a split)
  const bLeads = tally.down > tally.up;
  const score = (u, d) => (bLeads ? `${d}–${u}` : `${u}–${d}`);
  const where = measuredOnly ? "where both were measured" : `on the ${n} benchmark${n === 1 ? "" : "s"} both have a score on`;
  const say = !n ? (measuredOnly ? "No benchmark was measured on both models." : "No benchmark has a score for both models.")
    : tally.up === tally.down ? html`They split <span class="mono">${score(tally.up, tally.down)}</span> ${where}.`
    : html`${bLeads ? B.name : A.name} leads <span class="mono">${score(tally.up, tally.down)}</span> ${where}.`;
  const gA = p.general.get(A.id), gB = p.general.get(B.id);
  const gap = gA && gB ? (gA.g - gB.g) * 100 : null;   // A ahead of B in general performance, pct pts
  const overall = gap === null ? "" : Math.abs(gap) < 0.05 ? `even overall (#${p.rankOf.get(A.id)} vs #${p.rankOf.get(B.id)})`
    : `${gap > 0 ? A.name : B.name} is ${Math.abs(gap).toFixed(1)} pct pts ahead overall (#${p.rankOf.get(A.id)} vs #${p.rankOf.get(B.id)})`;
  const detail = [
    n && !measuredOnly ? `measured on both ${nm ? score(tally.mUp, tally.mDown) : "none"} · with an estimate ${score(tally.eUp, tally.eDown)}` : "",
    n ? `mean |Δ| ${pct(dsum / n)} pp` : "",
    overall,
  ].filter(Boolean).join(" · ");

  return html`
    <div class="controls cmp-show">${showSeg(() => { hideTip(); draw(); })}</div>
    <section class="cmp-verdict" aria-label="Verdict">
      ${fresh(`${A.id}:${B.id}:${prefs.show}:${detail}`, html`<div class="fresh"><p class="say">${say}</p><p class="muted">${detail}</p></div>`)}
      ${n ? split(A, B, tally, n) : nothing}
    </section>
    <section class="cmp-sec">
      <h2 class="h2">Every benchmark, by capability</h2>
      <p class="muted">Open a capability to see its benchmarks. The counts follow the filter.</p>
      ${legend()}
      ${capabilities(p, pair)}
    </section>`;
}

// the verdict's bar: A's wins from the left, B's from the right, the even ones between; solid where
// both were measured, hatched where an estimate is in it (the segments keep their elements: they slide)
function split(A, B, t, n) {
  const even = n - t.up - t.down;
  const seg = (cls, p, count, what) => html`<span class="${cls}" data-p="${p ?? nothing}" style="width:${(100 * count) / n}%" title="${count} ${what}"></span>`;
  return html`<div class="cmp-split">
      <div class="cmp-split-names">
        <span>${dot(A)}<span class="nm">${A.name}</span><b class="mono">${t.up}</b></span>
        <span><b class="mono">${t.down}</b><span class="nm">${B.name}</span>${dot(B)}</span>
      </div>
      <div class="cmp-split-bar" role="img" aria-label="${`${t.up} to ${A.name}, ${t.down} to ${B.name}${even ? `, ${even} even` : ""}`}">
        ${seg("m", A.provider, t.mUp, `won by ${A.name}, measured on both`)}${seg("e", A.provider, t.eUp, `won by ${A.name}, with an estimate`)}${seg("even", null, even, "even")}${seg("e", B.provider, t.eDown, `won by ${B.name}, with an estimate`)}${seg("m", B.provider, t.mDown, `won by ${B.name}, measured on both`)}
      </div>
      ${t.eUp + t.eDown ? html`<p class="cmp-split-key"><span class="m"></span>measured on both <span class="e"></span><i>with an estimate</i>${even ? html` <span class="even"></span>even` : nothing}</p>` : nothing}
    </div>`;
}

// the column heads over the mirrored bars (on phones: which bar is whose)
const flyHead = (A, B) => html`<div class="fly fly-head">
    <span class="ha">${fresh(A.id, html`<span class="fresh">${A.name}</span>`)} <span class="dot" data-p="${A.provider}" aria-hidden="true"></span></span>
    <span class="hn">benchmark · gap</span>
    <span class="hb"><span class="dot" data-p="${B.provider}" aria-hidden="true"></span> ${fresh(B.id, html`<span class="fresh">${B.name}</span>`)}</span>
    <span class="hp">Top bar: ${A.name} · bottom bar: ${B.name}</span>
  </div>`;

// one benchmark's two scores as mirrored bars: A grows to the left, B to the right, the name and
// the gap between them (pointing to the leader)
function fly(b, x, y, A, B) {
  const d = x && y ? (x.v - y.v) * 100 : null;
  const est = (x && x.s === "e") || (y && y.s === "e");
  const bar = (s) => (s ? html`<span class="bar ${s.s === "e" ? "e " + s.tier : "m"}" style="width:${Math.min(100, s.v * 100)}%"></span>` : nothing);
  const val = (s, m, side) => (s
    ? html`<div class="fv ${side}${s.s === "e" ? " est" : ""}" data-tip="${m.id}:${b.id}" tabindex="0">${fresh(`${s.s}${s.v}`, html`<span class="fresh">${s.s === "e" ? "≈" : ""}${pct(s.v)}%</span>`)}</div>`
    : html`<div class="fv ${side} none">—</div>`);
  let delta;
  if (d === null) delta = html`<span class="fd est fresh">one side only</span>`;
  else if (Math.abs(d) < 0.05) delta = html`<span class="fd fresh">even</span>`;
  else {
    const lead = d > 0 ? A : B, num = `${est ? "≈" : ""}${Math.abs(d).toFixed(1)}`;
    delta = html`<span class="fd fresh${est ? " est" : ""}"><span class="to-a" aria-hidden="true">${d > 0 ? "◀ " : ""}</span><span class="dot" data-p="${lead.provider}" aria-hidden="true"></span>${num}<span class="to-b" aria-hidden="true">${d < 0 ? " ▶" : ""}</span><span class="sr"> pp, ${lead.name} ahead</span></span>`;
  }
  return html`<div class="fly">
      <div class="ftrack l" data-p="${A.provider}">${bar(x)}</div>
      ${val(x, A, "a")}
      <div class="fname"><a href="${benchHref(b)}">${b.label}</a>${fresh(d === null ? "" : `${d.toFixed(1)}${est}${A.id}`, delta)}</div>
      ${val(y, B, "b")}
      <div class="ftrack r" data-p="${B.provider}">${bar(y)}</div>
    </div>`;
}

// every capability either model has a score in, folded to a line saying who leads it
function capabilities(p, pair) {
  const { A, B } = p;
  // a benchmark shows if each score it has passes the filter (the leaderboard's Show)
  const shows = ([, x, y]) => (x || y) && (!x || visible(x)) && (!y || visible(y));
  const caps = ix.benchesByCap.map(({ cap, benches }) => {
    const all = benches.map(pair).filter(([, x, y]) => x || y);
    const rows = all.filter(shows);
    const both = rows.filter(([, x, y]) => x && y);
    return { cap, all, rows, both };
  }).filter((c) => c.all.length);
  open ??= new Set(caps.length ? [caps.reduce((best, c) => (c.both.length > best.both.length ? c : best)).cap.id] : []);
  const toggle = (id) => { open.has(id) ? open.delete(id) : open.add(id); hideTip(); draw(); };

  return repeat(caps, ({ cap }) => cap.id, ({ cap, all, rows, both }) => {
    const up = both.filter(([, x, y]) => x.v - y.v > 0.0005).length, down = both.filter(([, x, y]) => y.v - x.v > 0.0005).length;
    const mean = both.length ? both.reduce((t, [, x, y]) => t + (x.v - y.v) * 100, 0) / both.length : 0;
    const oneSided = rows.length - both.length;
    let summary;
    if (!rows.length) summary = "Nothing to show with this filter";
    else if (!both.length) summary = `Only ${rows[0][1] ? A.name : B.name} has scores here`;
    else {
      summary = up > down ? `${A.name} ahead on ${up} of ${both.length}` : down > up ? `${B.name} ahead on ${down} of ${both.length}` : `Split ${up}–${down}`;
      if (Math.abs(mean) >= 0.05) {
        const leadsByCount = up >= down ? mean > 0 : mean < 0;
        summary += ` · ${up === down ? (mean > 0 ? A.name : B.name) + " " : ""}${Math.abs(mean).toFixed(1)} pp ${up === down || leadsByCount ? "higher" : "lower"} on average`;
      }
      if (oneSided) summary += ` · ${oneSided} one-sided`;
    }
    const isOpen = open.has(cap.id), hidden = all.length - rows.length;
    const lean = both.length && Math.abs(mean) >= 0.05
      ? html`<span class="${mean > 0 ? "a" : "b"}" data-p="${(mean > 0 ? A : B).provider}" style="width:${Math.min(50, Math.abs(mean) * 5)}%"></span>`
      : nothing;
    return html`<div class="cmp-cap">
        <button type="button" aria-expanded="${isOpen}" @click=${() => toggle(cap.id)}>
          <span class="cap-name">${cap.label}<span class="mono">${rows.length}</span></span>
          ${fresh(summary, html`<span class="cap-sum fresh">${summary}</span>`)}
          <span class="cmp-lean" aria-hidden="true">${lean}</span>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        ${isOpen ? html`<div class="cmp-rows">
            ${rows.length ? flyHead(A, B) : nothing}
            ${repeat(rows, ([b]) => b.id, ([b, x, y]) => fly(b, x, y, A, B))}
            ${hidden ? html`<p class="cmp-hidden">${hidden} more hidden by the filter</p>` : nothing}
          </div>` : nothing}
      </div>`;
  });
}
