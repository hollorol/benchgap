/* One model across every benchmark. */
import { html, nothing } from "../vendor/lit-html.js";
import { D, ix, pct, remember, methodLabel, providerLabel, benchHref, setMeta, show } from "../core.js";
import { sourceList, fitHref } from "../tip.js";
import { dot, confidenceFlag, legend, track, renderNotFound } from "../ui.js";

// the model page's lede (Pages::modelLead)
function modelLead(m) {
  return `As of ${D.meta.retrieved_at}, ${m.name} (${providerLabel(m.provider)}) has measured scores on ${m.n_measured} benchmark${m.n_measured === 1 ? "" : "s"}`
    + (m.n_estimated ? ` and estimated scores on ${m.n_estimated} more` : "") + ".";
}

// data: the model's scores (data/model/...)
export function renderModel(data, slug) {
  const m = ix.modelBySlug.get(slug);
  if (!m || !data) return renderNotFound(`No model “${slug}”.`);
  setMeta(`${m.name} benchmark scores`, `${m.name} benchmark scores: measured on ${m.n_measured} benchmark${m.n_measured === 1 ? "" : "s"}`
    + (m.n_estimated ? `, estimated on ${m.n_estimated} more, with the error and confidence of each estimate.` : "."));
  const scores = remember(data.scores);   // on the listed benchmarks
  const byB = new Map(scores.map((s) => [s.b, s]));
  const est = scores.filter((s) => s.s === "e");
  const nTier = (t) => est.filter((s) => s.tier === t).length;
  const row = (b) => {
    const s = byB.get(b.id);
    if (!s) {
      return html`<div class="mrow gap"><div class="bn"><a href="${benchHref(b)}">${b.label}</a></div><div class="track">not measured · no calibrated source to estimate from</div><div class="val">—</div></div>`;
    }
    const e = s.s === "e";
    const fit = fitHref(s) ? html` · <a href="${fitHref(s)}">see the fit</a>` : nothing;
    const why = e
      ? html`<div class="why">${confidenceFlag(s.tier)} estimated from ${sourceList(s, true)} via ${methodLabel(s.method)}, ± ${pct(s.sd)} pp${fit}${
          s.why.length ? html`<span>· ${s.why.join("; ")}</span>` : nothing
        }</div>`
      : nothing;
    return html`<div class="mrow ${e ? "e" : "m"}" data-p="${m.provider}">
              <div class="bn"><a href="${benchHref(b)}">${b.label}</a></div>
              <div class="track" data-tip="${m.id}:${b.id}" tabindex="0">${track(s, (v) => v * 100, [0.25, 0.5, 0.75])}</div>
              <div class="val">${e ? "≈" : ""}${pct(s.v)}%</div>${why}</div>`;
  };
  const blocks = ix.benchesByCap.map(({ cap, benches }) => {
    const nHere = benches.filter((b) => byB.has(b.id)).length;
    return html`<section class="cap-block"><h3 class="h3">${cap.label} <span class="muted mono" style="font-weight:400;font-size:.78rem">${nHere}/${benches.length}</span></h3>${benches.map(row)}</section>`;
  });

  show(html`<div class="page">
      <header class="page-head reveal">
        <div class="eyebrow">${providerLabel(m.provider)} · model</div>
        <div class="model-head">
          <h1 class="h2 who" data-p="${m.provider}">${dot(m)}${m.name}</h1>
          <dl class="kv">
            <dt>measured</dt><dd>${m.n_measured} benchmarks</dd>
            <dt>estimated</dt><dd><i>${est.length}</i> (${nTier("high")} high, ${nTier("medium")} medium, ${nTier("low")} low confidence)</dd>
          </dl>
        </div>
        <p class="lede">${modelLead(m)}</p>
      </header>
      ${legend()}
      ${blocks}
    </div>`);
}
