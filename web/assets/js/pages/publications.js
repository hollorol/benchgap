/* The papers on predicting benchmark scores, and how benchgap relates to each; the same content
 * is the static page in src/Pages.php. */
import { html } from "../vendor/lit-html.js";
import { setMeta, show } from "../core.js";
import { pageHead } from "../ui.js";

const PAPERS = [
  ["You Don't Need to Run Every Eval", "Zeng & Papailiopoulos", 2026, "2606.24020", "a model's other benchmark scores",
    "the model × benchmark score matrix is nearly rank-2; matrix completion in logit space (BenchPress)",
    "the closest relative: the same gapfilling problem with one global factor model. benchgap keeps local, explicit calibrations per benchmark pair, each with its own cross-validated error, and refuses to transfer across capabilities."],
  ["Sloth: scaling laws for LLM skills to predict multi-benchmark performance across families", "Polo et al.", 2024, "2412.06540", "training compute and latent skills",
    "scaling laws over low-dimensional skill factors, within and across model families",
    "predicts hypothetical models and needs training metadata. benchgap maps an existing model from its measured scores alone, which is all closed API models publish."],
  ["Observational Scaling Laws and the Predictability of Language Model Performance", "Ruan et al.", 2024, "2405.10938", "simple benchmarks and compute",
    "a latent capability variable regressed onto downstream benchmarks",
    "the same score-from-scores idea, anchored to compute. benchgap is compute-agnostic, so it also works for models whose training details are unknown."],
  ["From Benchmarks to Skills: Low-Rank Factors for LLM Evaluation", "Maimon et al.", 2025, "2507.20208", "a subset of a model's scores",
    "psychometric low-rank factorization; profiling a model from a few tasks",
    "closest in the fill-the-profile goal, but in latent space. benchgap stays in observable benchmark space and shows the fitted curve for every pair."],
  ["Efficient Benchmarking Is Just Feature Selection and Multiple Regression", "Bowyer et al.", 2026, "2605.25773", "a small coreset of benchmark items",
    "feature selection plus regression to predict full-benchmark scores",
    "the item-level analogue of the multivariate view's elastic net, whose lasso part selects the useful benchmarks."],
  ["metabench: A Sparse Benchmark of Reasoning and Knowledge in Large Language Models", "Kipnis et al.", 2024, "2407.12844", "a sparse (~3%) subset of items",
    "item-level distillation that preserves scores and rankings",
    "item-level. benchgap works from published aggregate scores, so it needs no access to benchmark items at all."],
  ["Look Before you Leap: Estimating LLM Benchmark Scores from Descriptions", "Park et al.", 2025, "2509.20645", "a redacted text description of the task",
    "an LLM as the regressor (the PRECOG corpus); no evaluation runs at all",
    "predicts before any evaluation exists; benchgap predicts after a model has some measured scores. Complementary ends of the pipeline."],
  ["How predictable is language model benchmark performance?", "Owen", 2024, "2401.04757", "training compute",
    "empirical analysis of benchmark performance across five orders of magnitude of compute",
    "a different input: predictability against compute, not scores from scores."],
  ["How Benchmark Prediction from Fewer Data Misses the Mark", "Zhang et al.", 2025, "2506.07673", null,
    "a systematic evaluation of 11 score-prediction methods across 19 benchmarks",
    "the caution this site's guardrails are built around: predictors fail on models unlike their calibration set."],
  ["PredictaBoard: Benchmarking LLM Score Predictability", "Pacchiardi et al.", 2025, "2502.14445", null,
    "benchmarks score predictability itself, via assessors that anticipate a model's errors",
    "instance-level predictability rather than score-level estimation; a complementary lens on the same uncertainty."],
];
// what sets benchgap apart from the papers above
const RELATED_WORK = [
  html`<b>Capability gating.</b> A factor model imputes between any two benchmarks. benchgap calibrates only within a capability group, so a model never evaluated on vision keeps that gap instead of inheriting an estimate from text benchmarks.`,
  html`<b>No estimate recursion.</b> Every input to an estimate is a measured score; an estimate never feeds another estimate. A factor model completes a matrix that already contains its own outputs.`,
  html`<b>Per-cell error.</b> Each estimate carries its own leave-one-out error and confidence level, and a pair whose best curve still fits poorly keeps no mapping at all. The papers above report one aggregate error over held-out cells.`,
  html`<b>The shared limit.</b> As How Benchmark Prediction from Fewer Data Misses the Mark shows, every method in this line misestimates models unlike its calibration set. Confidence levels flag the known risk factors - extrapolation, small fits, weak R² - but nothing here detects a genuinely novel model.`,
];

// the multivariate view against the papers above (paragraphs)
const MULTIVARIATE_WORK = [
  html`Predicting one benchmark from several others jointly is where benchgap meets the papers above head-on: BenchPress, Sloth and From Benchmarks to Skills do the same thing through latent factors over the whole score matrix. The <a href="/multivariate">multivariate view</a> does it with explicit features - the measured benchmarks themselves, named in every fit - selected per target benchmark.`,
  html`The selection echoes Efficient Benchmarking Is Just Feature Selection and Multiple Regression, one level up: they select items, benchgap selects benchmarks. An elastic net whose lasso part zeroes the useless candidates runs alongside a greedy forward search trying every one; on the features they find, a linear fit and a multivariate Michaelis–Menten curve compete by cross-validated error.`,
  html`What a joint model has and a per-target fit does not is strength borrowed across all benchmarks at once - BenchPress finds most of the score matrix is two numbers per model. benchgap trades that for fits a reader can check: every feature is a real benchmark, and every fit carries its own cross-validated error and a measured-vs-predicted scatter. These mappings are the deterministic precursor of that joint model: a Bayesian network over benchmark scores, imputing every gap with one coherent posterior, is where the roadmap points.`,
];

export function renderPublications() {
  setMeta("Publications: the research behind benchgap", "Papers on predicting LLM benchmark scores from other benchmarks - matrix completion, scaling laws, latent factors - and how benchgap relates to each.");
  const rows = PAPERS.map(([title, authors, year, id, from, approach, relation]) => html`<tr>
        <td><a href="https://arxiv.org/abs/${id}" rel="noopener" target="_blank">${title}</a><br><span class="muted">${`${authors}, ${year}`}</span></td>
        <td data-label="Predicts from">${from || "—"}</td>
        <td data-label="Approach">${approach}</td>
        <td data-label="How benchgap relates">${relation}</td></tr>`);
  show(html`<div class="page">
      ${pageHead("Publications", "The research behind the gapfilling", "benchgap is one entry in an active research line: predicting a model's benchmark scores without running every evaluation. These are the papers closest to what this site does, and how they relate to it.")}
      <section class="section"><div class="list-wrap"><table class="list papers">
        <thead><tr><th>Paper</th><th>Predicts from</th><th>Approach</th><th>How benchgap relates</th></tr></thead>
        <tbody>${rows}</tbody></table></div></section>
      <section class="section"><h2 class="h2">The multivariate predictions</h2>
        <article class="prose">${MULTIVARIATE_WORK.map((p) => html`<p>${p}</p>`)}</article>
      </section>
      <section class="section"><h2 class="h2">Where benchgap differs</h2>
        <article class="prose"><ul>${RELATED_WORK.map((item) => html`<li>${item}</li>`)}</ul></article>
      </section></div>`);
}
