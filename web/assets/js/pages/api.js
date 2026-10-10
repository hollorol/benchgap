/* The public API's documentation (api/v1/), with a live "Try it" panel. */
import { html, nothing, guard } from "../vendor/lit-html.js";
import { REPO_URL, ix, $, pct, byCapability, capLabel, capOf, benchLabel, setMeta, show } from "../core.js";
import { pageHead, codeBox, copyButton, seg } from "../ui.js";

const API_BASE = new URL("/api/v1/", location.origin).href;
const API_ENDPOINTS = [
  ["index.json", "Entry point: build metadata, endpoint templates, confidence thresholds, capabilities."],
  ["benchmarks.json", "All benchmark versions."],
  ["benchmarks/{name}/{version}.json", "One benchmark with all its scores, highest first."],
  ["models.json", "All models."],
  ["models/{slug}.json", "One model with all its scores."],
  ["scores.json", "Every score, measured and estimated."],
  ["scores.csv", "The same scores flattened to CSV, for spreadsheets."],
  ["mappings.json", "Every calibration (fitted mapping between two benchmarks), lowest error first."],
  ["mappings/{id}.json", "One calibration with its training points, sampled curve and estimates."],
  ["harness-tax.json", "How much harnesses disagree on the same benchmark: families, pairs, aggregates, audit queue. Measured scores only."],
  ["harness-tax/{family_id}.json", "One harness-tax family with every per-model delta and its provenance."],
  ["openapi.json", "OpenAPI 3.1 description of this API."],
];
const API_FIELDS = {
  Score: [
    ["model", "string", html`Model slug, e.g. <code>gpt-6-astra</code>.`],
    ["benchmark", "string", html`Benchmark key <code>name/version</code>, e.g. <code>terminal-bench-4/current</code>.`],
    ["score", "number", "Fraction in [0, 1]. Multiply by 100 for percent."],
    ["source", '"measured" | "estimated"', "Measured scores come from a public leaderboard; estimates are predictions."],
    ["estimate", "Estimate | null", "Present only for estimated scores."],
  ],
  Estimate: [
    ["confidence", '"high" | "medium" | "low"', html`Confidence level; see <a href="/method">Method</a> for the rules.`],
    ["error_pp", "number", "Cross-validated error of the fit that made it, in percentage points (the ± on the site)."],
    ["reasons", "string[]", "Why the confidence is not high; empty when it is."],
    ["extrapolated", "boolean", "An input score lies outside the range the fit was trained on."],
    ["method, method_name", "string", html`Curve family, e.g. <code>mm_offset</code> / Michaelis–Menten + offset; multivariate fits end in <code>_mv</code>, e.g. <code>enet_mv</code> / elastic net.`],
    ["kind", '"univariate" | "multivariate"', "One source benchmark, or several combined."],
    ["mapping_id", "integer | null", html`The calibration used; resolve with <code>mappings/{id}.json</code>. Null for multivariate estimates, whose fits v1 does not serve.`],
    ["inputs", "{benchmark, score}[]", "The same model's measured scores the estimate was computed from."],
  ],
  Benchmark: [
    ["key, name, version", "string", html`Stable identifier <code>name/version</code> and its parts.`],
    ["label", "string", "Display name, e.g. Terminal-Bench 4.0."],
    ["capability", "string", "Capability group; benchmarks are only calibrated within one."],
    ["harness, source_url", "string", "Evaluation harness and the leaderboard the measured scores come from."],
    ["n_measured, n_estimated", "integer", "Number of scores of each kind."],
    ["listed", "boolean", "Shown on the site (has estimates and enough models)."],
    ["url, page", "string", "This resource in the API, and on the website."],
  ],
  Model: [
    ["slug, name", "string", "Stable identifier and display name."],
    ["provider, provider_name", "string", html`e.g. <code>openai</code> / OpenAI.`],
    ["n_measured, n_estimated", "integer", "Number of scores of each kind."],
    ["listed", "boolean", "Shown on the site (has scores on a listed benchmark)."],
    ["url, page", "string", "This resource in the API, and on the website."],
  ],
  Mapping: [
    ["id", "integer", "Calibration id (changes when the data is refitted)."],
    ["from, to", "string", "Source and target benchmark keys."],
    ["method, method_name, equation", "string", "Selected curve and its fitted equation (x, y as fractions)."],
    ["n_models, r2, rmse_pp, loo_rmse_pp", "number", html`Training size and fit quality; <code>loo_rmse_pp</code> becomes the estimates' <code>error_pp</code>.`],
    ["n_estimates", "integer", "Estimates this calibration produced."],
    ["train_range", "[min, max]", "Source scores the curve was fitted on."],
    ["points, curve", "array", "Detail endpoint only: training points and the sampled curve."],
    ["url, page", "string", "This resource in the API, and on the website."],
  ],
  HarnessFamily: [
    ["family_id, label", "string", html`Stable identifier, e.g. <code>terminal-bench-2-1</code>, and display name.`],
    ["capability", "string", "Capability group."],
    ["tier", '"agentic" | "knowledge_tool_free" | "protocol_layer" | null', "Agentic: depends on tools or scaffold; tool-free: no tool layer; protocol layer: run-protocol variants within one harness."],
    ["same_item_set", '"verified" | "needs_audit" | "weak_alignment"', "Whether the versions are known to share the same items."],
    ["status", '"active" | "candidate"', "Candidates (auto-detected, or awaiting review) stay out of the headline aggregates."],
    ["versions", "string[]", "The benchmark versions compared."],
    ["pair_type, audit_note, origin, url", "string", html`Pair kind, review note, <code>seed</code> or <code>auto</code>, and the family's endpoint.`],
  ],
  HarnessPair: [
    ["family_id, tier, capability, same_item_set, status", "string", "As on its family."],
    ["pair_type", '"cross_harness" | "protocol_variant"', "Different harnesses, or one harness under different protocols."],
    ["a, b", "object", "The two versions; every delta is a minus b."],
    ["n_models, low_overlap", "integer, boolean", "Models measured on both; under 5 is low overlap, kept out of aggregates."],
    ["mean_abs_pp, median_abs_pp, max_abs_pp", "number | null", "Size of the per-model differences, percentage points."],
    ["share_gt_5pp, share_gt_10pp", "number | null", "Fraction of models differing by more than 5 and 10 pp."],
    ["kendall_tau, n_rank_flips", "number | null, integer", "Rank agreement of the two harnesses, and model pairs they order differently."],
    ["n_positive, n_negative, sign_test_p, directionality", "number", "Sign of the deltas: counts, a two-sided sign test, and the share agreeing with the dominant sign (0.9 or more is systematic)."],
    ["deltas", "HarnessDelta[]", "Family endpoint only: every model's difference."],
  ],
  HarnessDelta: [
    ["model, name", "string", "Model slug and display name."],
    ["score_a, score_b", "number", "The two measured scores, fractions in [0, 1]."],
    ["delta_pp", "number", "(score_a − score_b) × 100."],
    ["retrieved_a, retrieved_b", "string | null", "When each score was retrieved."],
    ["family_id, a, b", "string, object", html`Audit queue only (<code>audit.outliers</code>, deltas beyond 20 pp): which pair it belongs to.`],
  ],
};

// Minimal syntax highlighter for the code samples (no external library):
// one regex pass per language; each match becomes a <span class="tk-…">.
const SAMPLE_LANG = { curl: "bash", Python: "python", JavaScript: "js", pandas: "python" };
const HL_KEYWORDS = {
  bash: ["curl"],
  python: ["import", "as", "for", "in", "if", "and", "continue"],
  js: ["const", "await"],
};
function highlight(code, lang) {
  const kw = HL_KEYWORDS[lang] || [];
  const comment = lang === "js" ? String.raw`\/\/[^\n]*` : String.raw`#[^\n]*`;
  // group names are the token classes (tk-com, tk-str, ...)
  const re = new RegExp(
    [
      `(?<com>${comment})`,
      String.raw`(?<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|https?:\/\/[^\s"']+)`,
      String.raw`(?<num>\b\d+(?:\.\d+)?\b)`,
      String.raw`(?<kw>\b(?:${kw.join("|")})\b)`,
      String.raw`(?<fn>\b[A-Za-z_]\w*(?=\())`,
    ].join("|"),
    "g"
  );
  const out = [];
  let last = 0;
  for (const m of code.matchAll(re)) {
    const cls = Object.keys(m.groups).find((k) => m.groups[k] !== undefined);
    out.push(code.slice(last, m.index), html`<span class="tk-${cls}">${m[0]}</span>`);
    last = m.index + m[0].length;
  }
  out.push(code.slice(last));
  return out;
}

// the code samples, on this site's API
function codeSamples() {
  const base = API_BASE;
  return {
    curl: `curl ${base}benchmarks/${ix.home}.json`,
    Python: `import requests

data = requests.get("${base}benchmarks/${ix.home}.json").json()
for s in data["scores"]:
    est = s["estimate"]
    if est and est["confidence"] == "low":
        continue  # skip low-confidence estimates
    print(s["model"], round(s["score"] * 100, 1), s["source"])`,
    JavaScript: `const res = await fetch("${base}models/gpt-6-astra.json");
const { model, scores } = await res.json();
const measured = scores.filter((s) => s.source === "measured");
console.log(model.name, measured.length, "measured scores");`,
    pandas: `import pandas as pd

df = pd.read_csv("${base}scores.csv")
measured = df[df.source == "measured"]
table = measured.pivot(index="model", columns="benchmark", values="score")`,
  };
}

// data: the "Try it" picker's calibrations and harness-tax families (data/api.json); a response
// arriving after the page was left or reopened (signal aborted) is dropped
export function renderApi(data, arg, signal) {
  setMeta("Public API", "Free JSON and CSV API for LLM benchmark scores, measured and estimated, with an OpenAPI 3.1 description.");
  const samples = codeSamples();
  // the "Try it" picker: an endpoint, then (for a template) which one by name, as
  // [optgroup label, [[value, text]]]; the value fills the template's {...} part. Every list
  // comes ordered: models by name, benchmarks by capability, calibrations lowest error first
  const capGroups = (items, capOfItem, option) => byCapability(items, capOfItem).map(([cap, xs]) => [capLabel(cap), xs.map(option)]);
  const tryItems = {
    "benchmarks/{name}/{version}.json": ix.benchesByCap.map(({ cap, benches }) => [cap.label, benches.map((b) => [b.key, b.label])]),
    "models/{slug}.json": [["", ix.listedModels.map((m) => [m.slug, m.name])]],
    "mappings/{id}.json": capGroups(data.mappings, (m) => capOf(m.from), (m) => [String(m.id), `${benchLabel(m.from)} → ${benchLabel(m.to)} (±${pct(m.loo)} pp)`]),
    "harness-tax/{family_id}.json": capGroups(data.families, (f) => f.capability, (f) => [f.family_id, f.label]),
  };
  const tryPath = (ep, item) => (tryItems[ep] && item ? ep.replace(/\{.*\}/, item) : ep);
  // an endpoint's first pick: ix.home if it is among its items, else the first; a build may have none of some (harness-tax families)
  const firstItem = (ep) => {
    const values = (tryItems[ep] || []).flatMap(([, opts]) => opts.map(([v]) => v));
    return values.includes(ix.home) ? ix.home : values[0] || "";
  };
  const itemOptions = (ep, item) => (tryItems[ep].length ? tryItems[ep]
    .map(([g, opts]) => {
      const options = opts.map(([v, t]) => html`<option value="${v}" .selected=${v === item}>${t}</option>`);
      return g ? html`<optgroup label="${g}">${options}</optgroup>` : options;
    }) : html`<option value="" disabled selected>none in this build</option>`);
  const fieldTable = (name) => html`<div class="card api-obj"><h3 class="h3">${name}</h3><table class="list"><tbody>${API_FIELDS[name]
    .map(([f, t, d]) => html`<tr><td class="mono">${f}</td><td class="mono muted">${t}</td><td>${d}</td></tr>`)}</tbody></table></div>`;

  const TRY_EP = "benchmarks/{name}/{version}.json";   // opened first, on the home page's benchmark
  const st = { lang: "curl", ep: TRY_EP, item: firstItem(TRY_EP), status: "", out: "Press “Send request” to fetch a live response." };
  const path = () => tryPath(st.ep, st.item);

  const send = async () => {
    const sent = path();
    st.status = "loading…";
    draw();
    const t0 = performance.now();
    try {
      const res = await fetch(API_BASE + sent, { cache: "no-cache", signal });
      const text = await res.text();
      if (signal.aborted) return;
      const ms = Math.round(performance.now() - t0);
      st.status = `${res.status} ${res.statusText || ""} · ${(text.length / 1024).toFixed(1)} KB · ${ms} ms`;
      st.out = res.ok && sent.endsWith(".json") ? JSON.stringify(JSON.parse(text), null, 2) : text;
    } catch (err) {
      if (signal.aborted) return;
      st.status = "request failed";
      st.out = String(err);
    }
    draw();
    $("#api-out").scrollTop = 0;
  };

  // the page; only the Try-it panel and the code sample change, the rest is drawn once (guard)
  function draw() {
    show(html`<div class="page">
      ${guard([], () => html`${pageHead("API · v1", "Public API", `Everything on this site is available as plain JSON (and CSV): every benchmark, model,
        score and calibration, and the harness-tax analysis, with each estimate's confidence level and error. Free, no key, readable from any origin.`)}

      <div class="api-base-bar">
        <span class="verb">GET</span>
        <code class="mono" id="api-base">${API_BASE}</code>
        ${copyButton("api-base", "Copy base URL")}
        <a class="spec-link" href="${API_BASE}openapi.json" target="_blank" rel="noopener" title="Machine-readable spec: import into Postman, Insomnia or Swagger UI, or generate a client">OpenAPI 3.1 spec ↗</a>
      </div>`)}

      <section class="section">
        <h2 class="h2">Quick start</h2>
        <div class="controls">${seg("Code sample language", Object.keys(samples).map((k) => [k, k]), st.lang, (lang) => { st.lang = lang; draw(); })}</div>
        ${guard([st.lang], () => codeBox("api-sample", "", highlight(samples[st.lang], SAMPLE_LANG[st.lang])))}
      </section>

      ${guard([], () => html`<section class="section">
        <h2 class="h2">Endpoints</h2>
        <p class="muted">All paths are relative to the base URL. Every JSON document except <code>openapi.json</code> also carries
        <code>api_version</code>, <code>generated_at</code>, <code>data_retrieved_at</code>, <code>harnesses</code> and <code>counts</code>.</p>
        <div class="list-wrap"><table class="list api-ep"><thead><tr><th></th><th>Path</th><th>Returns</th></tr></thead><tbody>${API_ENDPOINTS
          .map(([p, d]) => html`<tr><td><span class="verb">GET</span></td><td class="mono">${p.includes("{") ? p : html`<a href="${API_BASE + p}" target="_blank" rel="noopener">${p}</a>`}</td><td>${d}</td></tr>`)}</tbody></table></div>
      </section>`)}

      <section class="section">
        <h2 class="h2">Try it</h2>
        <div class="api-try">
          <label class="sr" for="api-ep">Endpoint</label>
          <select id="api-ep" class="select mono" @change=${(e) => { st.ep = e.target.value; st.item = firstItem(st.ep); draw(); }}>${guard([], () => API_ENDPOINTS
            .map(([p]) => html`<option value="${p}" ?selected=${p === TRY_EP}>${p}</option>`))}</select>
          <label class="sr" for="api-item">Which one</label>
          <select id="api-item" class="select" ?hidden=${!tryItems[st.ep]} @change=${(e) => { st.item = e.target.value; draw(); }}>${guard([st.ep], () => (tryItems[st.ep] ? itemOptions(st.ep, st.item) : nothing))}</select>
          <button type="button" class="btn" id="api-send" ?disabled=${Boolean(tryItems[st.ep]) && !st.item} @click=${send}>Send request</button>
        </div>
        <div class="api-url mono muted"><span class="verb">GET</span> <span id="api-url">${API_BASE + path()}</span></div>
        <div class="api-status mono muted" id="api-status">${st.status}</div>
        ${codeBox("api-out", "api-out", st.out)}
      </section>

      ${guard([], () => html`<section class="section">
        <h2 class="h2">Objects</h2>
        <div class="grid-2">${["Score", "Estimate", "Benchmark", "Model", "Mapping", "HarnessFamily", "HarnessPair", "HarnessDelta"].map(fieldTable)}</div>
      </section>

      <section class="section prose">
        <h2>Conventions</h2>
        <ul>
          <li><b>Scores are fractions</b> in [0, 1]; the site shows them as percentages. Fields ending in <code>_pp</code> (errors, harness-tax deltas) are already percentage points.</li>
          <li><b>Estimates are never inputs.</b> Every estimate is computed from the same model's <i>measured</i> scores, listed in <code>estimate.inputs</code>. The harness-tax analysis uses measured scores only and feeds no estimate.</li>
          <li><b>Listed:</b> the API serves every benchmark version and model, the site only the listed ones (<code>listed: true</code>). The <code>counts</code> are of listed ones, so <code>benchmarks.json</code> and <code>models.json</code> hold more.</li>
          <li><b>Identifiers:</b> benchmarks are addressed by <code>name/version</code>, models by slug and harness-tax families by <code>family_id</code>. These are stable across data updates; mapping ids are not.</li>
          <li><b>Freshness:</b> responses always reflect the current database; <code>generated_at</code> says when it was last rebuilt. Responses carry an ETag, so revalidating unchanged data is a cheap 304.</li>
          <li><b>Versioning:</b> <code>v1</code> only gains fields. Anything that would break a client goes to <code>/api/v2/</code>, with <code>v1</code> kept alongside it.</li>
          <li><b>Errors:</b> an unknown benchmark, model, mapping or harness-tax family is an HTTP 404, with a small JSON body.</li>
        </ul>
        <h2>Using the data</h2>
        <p>The measured scores are data from <a href="https://benchlm.ai/data" rel="noopener" target="_blank">BenchLM.ai</a>,
        licensed under <a href="https://creativecommons.org/licenses/by-nc/4.0/" rel="noopener" target="_blank">CC BY-NC 4.0</a>
        (non-commercial use, with credit); the estimates are benchgap's additions. Check those terms before republishing scores.
        Estimates are model-based predictions: if you show them, show them as estimates, ideally with
        <code>error_pp</code> and <code>confidence</code>, and link back to benchgap.</p>
        <p>The benchgap code is <a href="${REPO_URL}/blob/main/LICENSE" target="_blank" rel="noopener">MIT-licensed</a>.
        A machine-readable description of the API is in <a href="${API_BASE}openapi.json" target="_blank" rel="noopener">openapi.json</a>.</p>
      </section>`)}
    </div>`);
  }
  draw();
}
