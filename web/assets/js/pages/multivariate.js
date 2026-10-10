/* Each benchmark predicted from several others: the fits and their measured-vs-predicted plots. */
import { html, nothing, repeat, guard } from "../vendor/lit-html.js";
import { D, ix, $, pct, methodLabel, capLabel, capOf, benchHref, setMeta, show } from "../core.js";
import { pageHead, seg, swap, lossColor, capFilter, diagScatter } from "../ui.js";

// data: the multivariate view's fits (data/multivariate.json, Snapshot's compact rows)
export function renderMultivariate(data) {   // its lede is also in src/Pages.php
  setMeta("Multivariate LLM benchmark predictions", "Each LLM benchmark predicted from several others together, of any capability: the fitted model, its cross-validated error and every prediction.");
  const gate = D.meta.quality_gate.max_loo_pp;
  const fits = data.multivariate;
  const filter = capFilter(fits, (f) => capOf(f.to));
  const gain = (f) => (f.alone == null ? -Infinity : f.alone - f.loo);
  // gain: over the best of its benchmarks alone
  const SORTS = { gain: ["biggest gain", (a, b) => gain(b) - gain(a)], loo: ["lowest error", (a, b) => a.loo - b.loo] };
  const LIST_ROWS = 20;  // fits shown before "Show all"
  let cap = "all", sort = "gain";

  const term = (id) => {
    const b = ix.bench.get(id);
    return html`<span class="mv-term"><a href="${benchHref(b)}">${b.label}</a><small>${capLabel(b.capability)}</small></span>`;
  };
  // measured (x) against leave-one-out predicted (y)
  const scatter = (f) => diagScatter(f.points.filter((p) => p[2] != null).map(([m, obs, pred]) => {
    const mod = ix.model.get(m);
    return [obs, pred, `${mod ? mod.name : "?"}: measured ${pct(obs)}%, predicted ${pct(pred)}% (${pred >= obs ? "+" : "−"}${pct(Math.abs(pred - obs))} pp)`];
  }), "measured (%)", "predicted (%)", `Measured against cross-validated predicted ${ix.bench.get(f.to).label} scores`);
  function card(f) {
    const d = gain(f);
    const vs = f.alone == null ? nothing : html`<dt>best one alone</dt><dd>${pct(f.alone)} pp <span class="${d > 0 ? "mv-better" : "muted"}">(${d > 0 ? "−" : "+"}${pct(Math.abs(d))} pp)</span></dd>`;
    return html`<article class="card mv-fit">
        <div class="mv-info">
          <div class="mv-formula">${term(f.to)}${f.from.map((id, i) => html`<span class="mv-next"><span class="mv-op">${i ? "+" : "~"}</span>${term(id)}</span>`)}</div>
          <dl class="kv">
            <dt>fit</dt><dd>${methodLabel(f.method)}</dd>
            <dt>models</dt><dd>${f.n}</dd>
            <dt>R²</dt><dd>${f.r2.toFixed(3)}</dd>
            <dt>LOO-CV error</dt><dd><span class="scale-dot" style="background:${lossColor(f.loo * 100, gate)}"></span><b>${pct(f.loo)} pp</b></dd>
            ${vs}
          </dl>
          <p class="mv-flags">${f.passes ? nothing : html`<span class="flag low">fails the quality gate</span> `}${f.used ? html`<span class="flag x" data-tiptext="The estimates of ${ix.bench.get(f.to).label} come from a combination of benchmarks of its own capability">estimates use a combination</span>` : nothing}</p>
        </div>
        ${scatter(f)}
      </article>`;
  }
  function list(all) {
    const rows = filter.byCap.get(cap).slice().sort(SORTS[sort][1]);
    // the cards keep their elements by target: another sort or capability only moves them
    return html`${repeat(all ? rows : rows.slice(0, LIST_ROWS), (f) => f.to, (f) => guard([f], () => card(f)))}${!all && rows.length > LIST_ROWS ? html`<button type="button" class="btn more-fits" @click=${() => swap($("#mv-list"), list(true))}>Show all ${rows.length}</button>` : nothing}`;
  }

  const redraw = () => { draw(); swap($("#mv-list"), fits.length ? list(false) : html`<p class="muted">No multivariate fits in this build yet.</p>`); };
  // the page; the fits are drawn into it on their own (swap())
  const draw = () => show(html`<div class="page">
      ${pageHead("Multivariate", "Each benchmark from several others", html`For each benchmark, candidates of any capability (its best single predictors and the benchmarks sharing the most models with it) feed two searches combined - one elastic net fit whose lasso part zeroes the useless ones, and greedy forward selection trying every candidate - always ending with at least two. On what they find, the linear fit and a multivariate Michaelis–Menten curve compete by cross-validated error.
        Each plot shows every model measured on all of them: its measured score against the prediction of the fit to the other models.
        Shown for analysis: the estimates come from the <a href="/calibration">calibrations</a>.`)}
      <section class="section">
        ${filter.chips(cap, (id) => { cap = id; redraw(); })}
        <div class="mv-sort"><span class="ctl-label">Sort</span>${seg("Sort", Object.entries(SORTS).map(([k, [label]]) => [k, label]), sort, (k) => { sort = k; redraw(); })}</div>
        <div class="mv-list" id="mv-list"></div>
      </section></div>`);
  redraw();
}
