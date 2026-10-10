/* Two models side by side on every benchmark, or one and the frontier model closest to it. */
import { html, nothing } from "../vendor/lit-html.js";
import { D, ix, $, pct, load, modelUrl, remember, groupBy, benchHref, setMeta, show, go } from "../core.js";
import { dot, modelLink, pageHead, legend, renderNotFound } from "../ui.js";

// the page's lede (Pages::COMPARE_INTRO)
const LEDE = "Two models on every benchmark, measured scores and estimates alike, with how far apart they sit. "
  + "Or pick one model and a checkbox fills the other with the frontier model closest to it in general performance "
  + "- the mean percentile of its measured scores; the frontier is its strongest tenth, of one provider or of any. "
  + "Measured scores only decide the standings.";
const cmpHref = (a, b, from) =>
  `/compare/${encodeURIComponent(a)}${b ? "/" + encodeURIComponent(b) : (from && from !== "any" ? "/from/" + encodeURIComponent(from) : "")}`;
const providerName = (p) => D.meta.providers[p] || p;

// data: every model's general performance and the frontier (data/compare.json, Compare.php):
// general is the mean percentile of a model's measured scores across the listed benchmarks,
// the frontier is its strongest tenth. Two models side by side on every benchmark (/compare/<a>/<b>),
// or one model (/compare/<a>) and the frontier model closest to it - of one provider
// (/compare/<a>/from/<p>), or of any - a pick that follows the field. Returns true when the page
// was already open (only the models changed).
export function renderCompare(data) {
  const parts = location.pathname.split("/").slice(2).filter(Boolean).map(decodeURIComponent);
  const general = new Map(data.compare.general.map(([id, g, n]) => [id, { g, n }]));   // by model id
  const rankOf = new Map(data.compare.general.map(([id], i) => [id, i + 1]));          // strongest first
  const nStanding = data.compare.general.length;
  const frontier = data.compare.frontier.filter(([id]) => ix.model.has(id));          // strongest first
  const isFrontier = new Set(frontier.map(([id]) => id));
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

  // the two selects: the listed models, grouped by provider
  const byProvider = groupBy(ix.listedModels, (m) => providerName(m.provider));
  const select = (label, sel, disabled, onPick) => html`<label class="cmp-pick"><span class="ctl-label">${label}</span><select class="select" ?disabled=${disabled} @change=${(e) => onPick(e.target.value)}>${
    [...byProvider.keys()].sort().map((p) => html`<optgroup label="${p}">${byProvider.get(p).map((m) => html`<option value="${m.slug}" .selected=${m.id === sel.id}>${m.name} · ${m.n_measured} measured</option>`)}</optgroup>`)}</select></label>`;
  // the provider whose frontier model fills B: those with one, plus any
  const frontierProviders = [...new Set(frontier.map(([id]) => ix.model.get(id).provider))]
    .map((p) => [p, providerName(p)])
    .sort((x, y) => x[1].localeCompare(y[1]));
  const fromSelect = html`<label class="cmp-pick"><span class="ctl-label">Frontier of</span><select class="select" id="cmp-from" ?disabled=${!auto} @change=${(e) => go(cmpHref(A.slug, null, e.target.value))}>
      <option value="any" .selected=${from === "any"}>Any provider</option>
      ${frontierProviders.map(([p, label]) => html`<option value="${p}" .selected=${from === p}>${label}</option>`)}</select></label>`;

  const ctx = { general, rankOf, nStanding, isFrontier, auto, from };
  const inPlace = !!$("#cmp-body");
  const draw = (body) => show(html`<div class="page">
      ${pageHead("Model compare", "Two models, benchmark by benchmark", LEDE)}
      <div class="controls">
        ${select("Model A", A, false, (a) => go(cmpHref(a, auto ? null : B.slug, auto ? from : null)))}
        ${select("Model B", B, auto, (b) => go(cmpHref(A.slug, b)))}
        <label class="check"><input type="checkbox" .checked=${auto} @change=${(e) => go(e.target.checked ? cmpHref(A.slug, null, $("#cmp-from").value) : cmpHref(A.slug, B.slug))}> Closest frontier model
          <span class="muted" data-tiptext="Model B becomes the frontier model closest in general performance to model A, of the chosen provider, and follows them as the field moves. Pinned pairs keep their URL.">ⓘ</span></label>
        ${fromSelect}
      </div>
      ${legend()}
      <div id="cmp-body" aria-busy="${body ? "false" : "true"}">${body || nothing}</div>
    </div>`);
  draw(null);

  // both models' scores load in (cached by the model pages' own visits); the body draws when they are in
  const at = location.pathname;
  Promise.all([load(modelUrl(A.slug)), load(modelUrl(B.slug))]).then(([da, db]) => {
    if (location.pathname !== at) return;   // another page was opened meanwhile
    draw(compareBody(A, remember(da.scores), B, remember(db.scores), ctx));
  }, () => {
    if (location.pathname === at) draw(html`<p class="muted">The models’ scores could not be loaded. Please try again.</p>`);
  });
  return inPlace;
}

// the page body: the two models' standings, then every benchmark either has a score on
function compareBody(A, sa, B, sb, ctx) {
  const aBy = new Map(sa.map((s) => [s.b, s]));
  const bBy = new Map(sb.map((s) => [s.b, s]));
  const side = (m) => {
    const g = ctx.general.get(m.id);
    return html`<div class="cmp-side">
        <h2 class="h3 who" data-p="${m.provider}">${dot(m)}${modelLink(m)}${ctx.isFrontier.has(m.id) ? html`<span class="flag high">frontier</span>` : nothing}</h2>
        <dl class="kv">
          <dt>general</dt><dd>${g ? pct(g.g) : "—"}</dd>
          <dt>rank</dt><dd>${g ? `#${ctx.rankOf.get(m.id)} of ${ctx.nStanding}` : "—"}</dd>
          <dt>measured</dt><dd>${g ? `${g.n} listed` : "no standing"}</dd>
        </dl>
      </div>`;
  };
  // the standings: shared measured benchmarks only, and the overall difference
  let up = 0, down = 0, even = 0, dsum = 0, nBoth = 0;
  for (const x of sa) {
    const y = bBy.get(x.b);
    if (x.s !== "m" || !y || y.s !== "m") continue;
    nBoth++;
    if (x.v > y.v) up++; else if (x.v < y.v) down++; else even++;
    dsum += Math.abs(x.v - y.v);
  }
  const gA = ctx.general.get(A.id), gB = ctx.general.get(B.id);
  const gap = gA && gB ? (gB.g - gA.g) * 100 : null;   // B ahead of A in general performance, pct pts
  const mid = nBoth
    ? `On the ${nBoth} benchmark${nBoth === 1 ? "" : "s"} both are measured on, ${A.name} leads on ${up}, ${B.name} on ${down}${even ? `, ${even} even` : ""} · mean |Δ| ${pct(dsum / nBoth)} pp.`
    : "No benchmark both are measured on: the deltas below lean on at least one estimate.";
  const overall = gap === null ? "" : ` Overall, ${gap === 0 ? "they are even" : `${gap > 0 ? B.name : A.name} is ${Math.abs(gap).toFixed(1)} pct pts ahead`} (ranks #${ctx.rankOf.get(A.id)} vs #${ctx.rankOf.get(B.id)}).`;
  const note = ctx.auto
    ? html`<p class="muted cmp-note">${B.name} is the frontier model ${ctx.from !== "any" && B.provider === ctx.from ? `of ${providerName(ctx.from)} ` : ""}closest to ${A.name} in general performance${
      gap === null ? "" : ` (${Math.abs(gap).toFixed(1)} pct pts ${gap > 0 ? "better" : gap < 0 ? "worse" : "even"})`}; it follows the field - uncheck to pin this pair.</p>`
    : nothing;

  const val = (s) => (s ? `${s.s === "e" ? "≈" : ""}${pct(s.v)}%` : "—");
  const bar = (s, cls, m) => (s
    ? html`<span class="bar ${cls}${s.s === "e" ? " e " + s.tier : " m"}" data-p="${m.provider}" style="width:${Math.min(100, s.v * 100)}%"></span>`
    : nothing);
  const cell = (cls, s, m, b) => (s
    ? html`<div class="${cls}${s.s === "e" ? " est" : ""}" data-tip="${m.id}:${b.id}" tabindex="0">${val(s)}</div>`
    : html`<div class="${cls}">${val(s)}</div>`);
  const row = (b) => {
    const x = aBy.get(b.id), y = bBy.get(b.id);
    const est = (x && x.s === "e") || (y && y.s === "e");
    const d = x && y ? (x.v - y.v) * 100 : null;
    return html`<div class="crow">
        <div class="bn"><a href="${benchHref(b)}">${b.label}</a></div>
        <div class="ctrack">${bar(x, "a", A)}${bar(y, "b", B)}</div>
        ${cell("cva", x, A, b)}${cell("cvb", y, B, b)}
        <div class="dlt${d === null ? "" : d > 0 ? " up" : d < 0 ? " down" : ""}">${d === null ? "—" : `${est ? "≈" : ""}${d > 0 ? "+" : ""}${d.toFixed(1)}`}</div>
      </div>`;
  };
  const blocks = ix.benchesByCap.map(({ cap, benches }) => {
    const rows = benches.filter((b) => aBy.has(b.id) || bBy.has(b.id));
    if (!rows.length) return nothing;
    const shared = benches.filter((b) => aBy.has(b.id) && bBy.has(b.id)).length;
    return html`<section class="cap-block"><h3 class="h3">${cap.label} <span class="muted mono" style="font-weight:400;font-size:.78rem">${shared}/${benches.length} shared</span></h3>${rows.map(row)}</section>`;
  });
  return html`${note}<div class="cmp-sum">${side(A)}<p class="cmp-mid">${mid}${overall}</p>${side(B)}</div>${blocks}`;
}
