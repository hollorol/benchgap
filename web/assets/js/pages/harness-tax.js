/* The harness tax: how much the same benchmark's measured scores disagree across harnesses. */
import { html, repeat, guard } from "../vendor/lit-html.js";
import { $, pct, setMeta, show } from "../core.js";
import { hideTip } from "../tip.js";
import { pageHead, seg, swap, diagScatter } from "../ui.js";

// data: the harness-tax analysis (data/harness-tax.json, benchgap harness-tax): the pairs
// of the same benchmark measured under different harnesses, each with its metrics and
// per-model deltas. Measured scores only; nothing here feeds the estimates.
export function renderHarnessTax(data) {   // its lede is also in src/Pages.php
  setMeta("The harness tax: how much harnesses disagree",
    "How much the same benchmark's measured scores disagree across harnesses and run protocols. Measured scores only, never estimates.");
  const ht = data.harness_tax;
  const TIERS = { agentic: "agentic", knowledge_tool_free: "tool-free", protocol_layer: "protocol layer" };
  const deltasOf = (p) => ht.deltas[p.id] || [];
  // the biggest disagreement first (Snapshot); a pair no model was measured on both of has nothing to show
  const pairs = ht.pairs.filter((p) => deltasOf(p).length);
  const shortKey = (k) => (k || "").endsWith("/current") ? k.slice(0, -"/current".length) : k || "?";
  let tier = "all";

  // one pair's measured scores against each other
  function htScatter(p) {
    const ds = deltasOf(p);
    const a = shortKey(p.a.key), b = shortKey(p.b.key);
    return diagScatter(ds.map((d) => [d.score_a, d.score_b,
      `${d.name}: ${pct(d.score_a)}% on ${a}, ${pct(d.score_b)}% on ${b} (Δ ${d.delta_pp >= 0 ? "+" : "−"}${Math.abs(d.delta_pp).toFixed(1)} pp)`]),
    `${a} (%)`, `${b} (%)`, `${a} against ${b} measured scores`);
  }
  function htCard(p) {
    const name = (v) => html`${shortKey(v.key)} <small>· ${v.harness || "?"}</small>`;
    const flags = [
      p.low_overlap ? html`<span class="flag low">low overlap</span>` : "",
      p.same_item_set === "needs_audit" ? html`<span class="flag medium">item set needs audit</span>` : "",
      p.same_item_set === "weak_alignment" ? html`<span class="flag low">weak item-set alignment</span>` : "",
      p.status === "candidate" ? html`<span class="flag x">candidate family</span>` : "",
      p.pair_type === "protocol_variant" ? html`<span class="flag x">protocol variant</span>` : "",
      deltasOf(p).some((d) => Math.abs(d.delta_pp) > 20) ? html`<span class="flag low">audit queue</span>` : "",
    ].filter(Boolean).map((flag, i) => (i ? html` ${flag}` : flag));
    const m = (label, v) => html`<dt>${label}</dt><dd>${v}</dd>`;
    const n = p.n_positive + p.n_negative;
    return html`<article class="card mv-fit${p.low_overlap ? " ht-low" : ""}">
        <div class="mv-info">
          <div class="mv-formula">${name(p.a)}<span class="mv-op">vs</span>${name(p.b)}</div>
          <dl class="kv">
            ${m("models", p.n)}
            ${m("mean |Δ|", p.mean_abs_pp == null ? "—" : `${p.mean_abs_pp.toFixed(1)} pp`)}
            ${m("median |Δ|", p.median_abs_pp == null ? "—" : `${p.median_abs_pp.toFixed(1)} pp`)}
            ${m("max |Δ|", p.max_abs_pp == null ? "—" : `${p.max_abs_pp.toFixed(1)} pp`)}
            ${m("Kendall τ", p.kendall_tau == null ? "—" : p.kendall_tau.toFixed(2))}
            ${m("rank flips", p.n_rank_flips)}
            ${m("direction", n ? `${p.n_positive} up / ${p.n_negative} down` : "—")}
            ${m("sign test p", p.sign_p == null ? "—" : p.sign_p.toFixed(3))}
            ${m(">5 pp", p.share_gt_5 == null ? "—" : pct(p.share_gt_5, 0) + "%")}
            ${m(">10 pp", p.share_gt_10 == null ? "—" : pct(p.share_gt_10, 0) + "%")}
          </dl>
          <p class="mv-flags">${flags}</p>
        </div>
        ${htScatter(p)}
      </article>`;
  }
  function htList() {
    const shown = pairs.filter((p) => tier === "all" || p.tier === tier);
    // the cards keep their elements by pair: another tier only drops and moves them
    return shown.length ? repeat(shown, (p) => p.id, (p) => guard([p], () => htCard(p))) : html`<p class="muted">No pairs in this tier.</p>`;
  }
  const agg = ht.aggregates || {};
  const tiers = agg.by_tier || {};
  const tierLine = Object.entries(tiers).map(([t, s]) => `${TIERS[t] || t}: ${s.pooled_mean_abs_pp == null ? "n/a" : s.pooled_mean_abs_pp.toFixed(1) + " pp"}`).join(" · ");
  const ratio = agg.headline && agg.headline.ratio;
  const redraw = () => { draw(); swap($("#ht-list"), htList()); };
  // the page; the pairs are drawn into it on their own (swap())
  const draw = () => show(html`<div class="page">
      ${pageHead("Harness tax", "The same benchmark, measured differently", "The same benchmark, run under different harnesses or protocols, disagrees about the same models - on the agentic benchmarks by far more than on the tool-free ones. Measured scores only: no estimate enters this page. Each point below is one model's score under the one harness against the other; the diagonal is agreement.")}
      <section class="section">
        <div class="mv-sort"><span class="ctl-label">Tier</span>${seg("Tier", [["all", "all"], ...Object.entries(TIERS)], tier, (k) => { tier = k; hideTip(); redraw(); })}</div>
        <p class="muted">Pooled mean |Δ| over reportable pairs - ${tierLine || "n/a"}. Verified families only, the agentic vs tool-free ratio: <b>${ratio == null ? "n/a" : ratio.toFixed(1) + "x"}</b>.</p>
        <div class="mv-list" id="ht-list"></div>
      </section></div>`);
  redraw();
}
