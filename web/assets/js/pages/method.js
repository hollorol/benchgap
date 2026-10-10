/* The method: how the gaps are filled, and when not to trust it. */
import { html } from "../vendor/lit-html.js";
import { ABOUT, D, pct, setMeta, show } from "../core.js";
import { pageHead } from "../ui.js";

// the end-to-end validation section: the holdout evaluation's headline numbers, from the site
// data (benchgap holdout stores them; Pages::rules says the same); absent until it has been run
function validated() {
  const h = D.holdout;
  if (!h) return html`<p>The pipeline's end-to-end validation (masking measured scores, refitting everything and
      scoring the masked cells against their truth) has not been stored in this database yet.</p>`;
  const lv = h.by_level_mae_pp;
  return html`<p>The whole pipeline was tested by masking a share of the measured scores, wiping every fitted
      table, refitting everything on what's left and scoring the masked cells against their held-out truth.
      On ${h.n_runs} runs (${h.random_mae_pp[0].toFixed(1)} ± ${h.random_mae_pp[1].toFixed(1)} pp MAE on the random-mask seeds),
      the estimates fill ${pct(h.random_coverage[0], 0)}%–${pct(h.random_coverage[1], 0)}% of masked cells at
      <b>${h.random_mae_pp[0].toFixed(1)} pp</b> mean absolute error — ${h.pipeline_vs_svd2_pct.toFixed(0)}% lower than a
      matrix-completion baseline on the same cells, whose error on the cells the pipeline refuses is nearly double its own.
      The confidence levels above are correctly ordered — realized error ${lv.high.toFixed(1)} / ${lv.medium.toFixed(1)} /
      ${lv.low.toFixed(1)} pp (high/medium/low) — but the ± labels are selected minima and understate the realized RMSE
      by roughly 40–70% (×${h.label_inflation_rmse.high.toFixed(1)}–${h.label_inflation_rmse.medium.toFixed(1)}).
      Reweighted to the confidence mix of the published matrix, a published estimate should be expected to carry about
      <b>${h.reweighted.mae_pp.toFixed(1)} pp MAE</b> (${h.reweighted.rmse_pp.toFixed(1)} pp RMSE), and a model with a single
      measured score gets estimates for only ${pct(h.sparse_coverage.k1, 0)}% of its remaining benchmarks
      (${h.sparse_mae_pp.k1.toFixed(1)} pp MAE where it does).</p>`;
}

export function renderMethod() {
  const r = D.meta.confidence_levels, g = D.meta.quality_gate, c = D.meta.counts.confidence;
  setMeta("How missing benchmark scores are estimated", "How benchgap estimates missing LLM benchmark scores: calibration curves, multivariate mappings, leave-one-out validation and confidence levels.");
  show(html`<div class="page">
      ${pageHead("Method", "How the gaps are filled, and when not to trust it", ABOUT)}
      <nav class="jump chip-row" aria-label="On this page">${[["data", "The data"], ["calibrating", "Calibrating"], ["together", "Several together"], ["filling", "Filling a gap"], ["confidence", "Confidence"], ["validated", "Validated"], ["views", "Analysis views"], ["harness", "Harness tax"], ["caveats", "Caveats"]]
        .map(([id, label]) => html`<a class="chip" href="#${id}">${label}</a>`)}</nav>
      <article class="prose">
        <h2 id="data">The data</h2>
        <p>Measured scores come from public evaluation leaderboards and model reports, compiled by
        <a href="https://benchlm.ai/data" rel="noopener" target="_blank">BenchLM.ai</a> (harness: ${D.meta.harnesses.join(", ")}),
        retrieved ${D.meta.retrieved_at || ""}. Every score is stored as a fraction and shown as a percentage. Each benchmark belongs to a
        <b>capability</b> group (agentic terminal, agentic tools, knowledge, vision, …). Benchmarks are only ever calibrated against
        benchmarks of the same capability: a model never run on a vision benchmark keeps that gap instead of inheriting a score from text benchmarks.</p>

        <h2 id="calibrating">Calibrating one benchmark against another</h2>
        <p>For every ordered pair of same-capability benchmarks with at least ${g.min_pairs} models measured on both, ${g.n_candidates} monotone curve families are fitted
        by least squares: linear, Michaelis–Menten (with and without an offset), the inverse Michaelis–Menten form, Hill and an offset logistic.
        The saturating forms capture the typical shape: gains on an easier benchmark flatten out while a harder one keeps discriminating.</p>
        <div class="formula">y = y₀ + V<sub>max</sub> · x / (K + x)</div>
        <p>The curve with the lowest <b>leave-one-out cross-validated error</b> is kept: each model is held out in turn, the curve is refitted without it,
        and the held-out score is predicted. That error, in percentage points, is the “±” shown next to every estimate. A pair keeps no mapping at all
        unless its best curve reaches R² ≥ ${g.min_r2} and an error of at most ${g.max_loo_pp} pp; poorly fitting pairs leave their gaps empty rather than filling them with noise.</p>

        <h2 id="together">Several benchmarks together</h2>
        <p>One benchmark often does not pin a score down, so for each target a <b>multivariate mapping</b> predicts it from several
        same-capability benchmarks at once. Two searches run and the one with the lower leave-one-out error is kept: an
        <b>elastic net</b> fitted over a growing pool of candidates, whose lasso part drives useless sources' coefficients to exactly zero,
        and a <b>greedy forward search</b> that tries every candidate at each step. On whatever features each lands, two families compete
        by the same cross-validated error: the linear elastic net, and a multivariate Michaelis–Menten curve - the sources combined into a
        weighted index, mapped through the same saturating shape as the univariate curves.</p>
        <p>A multivariate mapping is stored only if it passes the same quality gate and beats the target's best single calibration.
        A lasso-selected single source may be stored (on little overlap its shrinkage can beat every univariate curve), but a
        one-feature nonlinear fit is not - that is the univariate pipeline's job.</p>

        <h2 id="filling">Filling a gap</h2>
        <p>For a model missing a score, every mapping into that benchmark from a benchmark the model <i>was</i> measured on is a candidate; the one
        with the lowest cross-validated error wins. A multivariate mapping is preferred when the model is measured on all of its source
        benchmarks and its error is lower; a model missing one of the sources simply falls back to the univariate path.
        Estimates are never used to make further estimates: inputs are always measured scores.</p>

        <h2 id="confidence">Confidence levels</h2>
        <p>Every estimate gets a confidence level, so low-confidence fills are visibly different from the others on every page:</p>
        <table class="tier-table">
          <thead><tr><th>Level</th><th>Rule</th><th>Count</th></tr></thead>
          <tbody>
            <tr><td><span class="flag high">high</span></td><td>cross-validated error ≤ ${r.high_max_pp} pp and no warning below</td><td class="mono">${c.high}</td></tr>
            <tr><td><span class="flag medium">medium</span></td><td>error ≤ ${r.medium_max_pp} pp, or a high-confidence fit with one warning</td><td class="mono">${c.medium}</td></tr>
            <tr><td><span class="flag low">⚠ low</span></td><td>error above ${r.medium_max_pp} pp, or demoted by warnings</td><td class="mono">${c.low}</td></tr>
          </tbody>
        </table>
        <p>Each of these warnings demotes an estimate by one level:</p>
        <ul>
          <li><b>Extrapolated</b>: the model's source score lies outside the range the mapping was fitted on.</li>
          <li><b>Small sample</b>: the mapping was fitted on fewer than ${r.min_reliable_n} models, so its error estimate is itself noisy.</li>
          <li><b>Uninformative fit</b>: R² below ${r.min_informative_r2}; the curve explains little of how models differ on the target.</li>
        </ul>
        <p>Hover over, tap or focus any estimate to see exactly which benchmark it came from, the curve used, and which warnings applied.
        The leaderboard and matrix can hide low-confidence estimates (<i>+ reliable estimates</i>) or all of them (<i>Measured only</i>).</p>

        <h2 id="validated">Validated end to end</h2>
        ${validated()}

        <h2 id="views">Analysis views</h2>
        <p>Two pages show fits that never produce an estimate. The <a href="/calibration">calibration page</a> also shows how well benchmarks of
        <i>different</i> capabilities predict each other - curiosity only, since no estimate crosses a capability. The
        <a href="/multivariate">multivariate view</a> fits each benchmark from several of any capability and plots every model's
        cross-validated prediction. Both are analyses over the same measured scores; the estimates come only from the same-capability
        calibrations and multivariate mappings above.</p>

        <h2 id="harness">The harness tax</h2>
        <p>Each benchmark version records whose run it is: the model's own published numbers, or one of the evaluation harnesses'
        own runs (${D.meta.harnesses.join(", ")}). The same benchmark measured under two harnesses disagrees about the same models -
        by double-digit percentage points on the agentic benchmarks, by around a point on the tool-free knowledge ones.
        <a href="/harness-tax">The harness tax</a> measures this from measured scores only: pairs of the same benchmark's versions,
        per-model deltas with their provenance, and a sign test separating a systematic tax from noise. It never feeds the estimates;
        it is why each of them holds for the source leaderboard's evaluation setup only.</p>

        <h2 id="caveats">Caveats</h2>
        <ul>
          <li>Estimates are predictions, not measurements. A model can genuinely over- or under-perform what its other scores imply.</li>
          <li>Coefficients are harness-specific: these calibrations hold for the source leaderboard's evaluation setup, not for other harnesses.</li>
          <li>Saturating curves have a ceiling. Several models above a mapping's fitted range can receive the same estimate; those are flagged as extrapolated.</li>
          <li>The error bars are point estimates of typical error, not credible intervals. A probabilistic version is on the roadmap.</li>
        </ul>
      </article></div>`);
}
