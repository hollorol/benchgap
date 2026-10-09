<h1 align="center">bench<i>gap</i></h1>

<p align="center"><b>Mind the gap.</b><br>
The missing half of every LLM leaderboard.</p>

<p align="center">
  <a href="https://github.com/hollorol/benchgap/actions/workflows/deploy.yml"><img alt="Deploy" src="https://github.com/hollorol/benchgap/actions/workflows/deploy.yml/badge.svg"></a>
  <a href="https://github.com/hollorol/benchgap/actions/workflows/update-data.yml"><img alt="Update the data" src="https://github.com/hollorol/benchgap/actions/workflows/update-data.yml/badge.svg"></a>
  <a href="https://benchgap.net"><img alt="Live: benchgap.net" src="https://img.shields.io/badge/live-benchgap.net-c2410c"></a>
  <a href="https://benchgap.net/api"><img alt="API: OpenAPI 3.1" src="https://img.shields.io/badge/API-OpenAPI%203.1-0f7b55"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

Most language models are only ever run on a handful of benchmarks, so every
leaderboard is mostly empty cells. benchgap fills them, carefully. It
calibrates benchmarks against each other on the models measured on both,
predicts each missing score from that model's own measured results, and
attaches the cross-validated error and a confidence level to every estimate.
Estimates are never used to make other estimates, calibrations never cross
capabilities, and a gap that can't be filled honestly stays a gap.

**See it live at [benchgap.net](https://benchgap.net)**: leaderboards, the
full score matrix, every calibration curve, each benchmark predicted from
several others, and a public JSON API ([docs](https://benchgap.net/api)).

Bootstrapped from a prior analysis session that calibrated Terminal-Bench v4.0
scores onto the v2.1 scale and found that a Michaelis-Menten curve with offset

```
y = y0 + Vmax * x / (K + x)
```

fits far better (LOO-CV RMSE ~4 pp on the paired models) than linear or
quadratic alternatives. benchgap generalizes that to a multi-benchmark
database (every percentage-scale benchmark in the [BenchLM.ai](https://benchlm.ai/data)
data, refreshed daily), with mapping candidates fitted per version pair, model
selection by leave-one-out cross-validation, and gapfilled scores stored with
full provenance.

Deterministic least-squares today; probabilistic fitters later - see
"Probabilistic roadmap".

## Layout

```
benchgap/
├── data/
│   ├── benchlm/                 # BenchLM's data files (downloaded, not committed)
│   ├── seed/scores.csv          # long-format seed (scripts/build_seed.py; not committed)
│   └── benchgap.db              # the database the pipeline builds (init … gapfill)
├── scripts/
│   ├── build_seed.py            # downloads BenchLM's data, writes data/seed/scores.csv
│   └── load_database.sh         # publishes a build on the server (update-data.yml)
├── src/benchgap/
│   ├── db.py                    # schema + data access (capability, unit, multi-mappings)
│   ├── ingest.py                # seed CSV -> database
│   ├── fitting.py               # monotone univariate candidates, fit, LOO-CV, selection
│   ├── fit.py                   # mapping fits (within and across capabilities) + quality gate
│   ├── multivariate.py          # multivariate mappings and the multivariate view's fits
│   ├── families.py + families.json  # the harness-tax benchmark family registry (+ auto-detection)
│   ├── harness_tax.py           # how much harnesses disagree about the same models
│   ├── parallel.py              # independent fits on every core
│   ├── cache.py                 # fit results reused between runs (--cache)
│   ├── gapfill.py               # predict + store missing scores (source='gapfilled')
│   ├── report.py                # mapping summaries, score matrix
│   ├── html_report.py           # self-contained HTML report (matplotlib, base64 PNGs)
│   └── cli.py                   # command-line interface
├── web/                         # benchgap.net (see "Website")
│   ├── index.html, assets/      # front-end: app.js (router), js/ (a module per page, lit-html), style.css, fonts
│   ├── serve.php                # Slim 4 backend: pages, site data, public API, llms.txt
│   ├── .htaccess                # Apache: routing to serve.php, cache headers
│   ├── src/
│   │   ├── Snapshot.php         # the site data, built once per data update; confidence levels
│   │   ├── Site.php             # each page's data (/data/*.json)
│   │   ├── Api.php              # the public API (/api/v1)
│   │   ├── Pages.php            # each page's title, description and plain-HTML summary
│   │   └── Curves.php           # the fitted curves' equations and samples, for display
│   └── api/v1/openapi.json      # OpenAPI 3.1 description of the API
├── compose.yaml                 # the local dev stack (docker compose up)
├── docker/dev/Dockerfile        # its web server: Apache with PHP, as benchgap.net runs it
├── .github/workflows/           # deploy web/ on every push to main; refresh the data daily
└── tests/                       # pytest; tests/data/seed.csv is a fixed BenchLM subset
```

## Usage

```bash
uv venv && uv pip install -e ".[dev]"  # or: pip install -e ".[dev]"
python scripts/build_seed.py --fetch   # download BenchLM's newest results -> data/seed/scores.csv
benchgap init                          # create data/benchgap.db
benchgap ingest                        # load data/seed/scores.csv
benchgap fit -v                        # fit univariate mappings per version pair (every core; -j N)
benchgap multifit                      # fit multivariate mappings per target
benchgap crossfit                      # fit pairs across capabilities (cross-domain view only)
benchgap crossmultifit                 # fit each benchmark from several of any capability (multivariate view only)
benchgap harness-tax                   # measure how much harnesses disagree (data/harness_tax.json + history)
uv run python scripts/export_paper.py  # regenerate exports/paper/ from data/harness_tax.json
benchgap fit --cache .fit-cache        # reuse the fits whose data did not change since the last run (any fit command)
benchgap gapfill                       # fill missing scores (prefers multi where it wins)
benchgap report                        # dense-core score matrix (terminal)
benchgap html                          # self-contained HTML report -> data/report.html
benchgap predict terminal-bench-4/current aa-terminal-bench4/current 59.6
benchgap report --min-models 12 --min-benchmarks 5   # denser view (0 disables filtering)
pytest                                 # run the tests (~20 s; tests/data/seed.csv)
docker compose up                      # the website locally on http://localhost:8080 (see "Website")
```

The matrices show a **dense-core view** by default: benchmark versions with
fewer than `--min-models` (default 8) measured models and models measured on
fewer than `--min-benchmarks` (default 3) benchmarks are peeled iteratively
until both thresholds hold, which minimizes empty cells. This is display-only - the database, mappings,
gapfill, and all other report sections keep the full dataset; pass 0 for
either threshold to see everything.

The HTML report (`benchgap html`, needs the `report` extra: `pip install
".[report]"`) is a single self-contained file: database stats, mapping
summary, a color-coded **predictability matrix** (rows = source benchmark,
columns = target, cell color = LOO CV RMSE of the best mapping from green
to red across the 0-15 pp quality-gate range; gray = no usable mapping,
dark = diagonal, blank = cross-capability by design), multivariate
mappings, per-mapping fit figures (paired scores, all candidate curves,
residuals, gapfilled points marked), and the full score matrix with
gapfilled cells highlighted. Charts are embedded as base64 PNGs, so the
file works offline and can be shared as-is.

The tests run on `tests/data/seed.csv`, a fixed 20-benchmark subset of the
BenchLM data, so they stay fast and do not change with the daily data; the
web tests need PHP and `web/vendor` (`composer install --working-dir web`).
The deploy workflow runs them before every upload.

Run `benchgap` from the repository root (paths are relative to the working
directory); pass `--db` to use a different database file.

## Website (benchgap.net)

[benchgap.net](https://benchgap.net) lives in `web/`: a plain JavaScript front-end
(ES modules, with [lit-html](https://lit.dev/docs/libraries/standalone-templates/)
as its only library) and a small [Slim 4](https://www.slimframework.com/) backend (`serve.php`)
that computes the site's data and the public API from the benchgap database.
Every push to `main` deploys `web/`
(`.github/workflows/deploy.yml`), and every day `.github/workflows/update-data.yml`
downloads BenchLM's newest results, recomputes every calibration and estimate,
and publishes them; pages, the API and llms.txt show the new data and its
dates right away. Run it by hand with `gh workflow run update-data.yml`.

To run the site locally, with Docker:

```bash
docker compose up        # http://localhost:8080
```

It serves `web/` as benchgap.net does (Apache with PHP, `web/.htaccess`) from the
database at `data/benchgap.db`: build it with the pipeline (Usage), or put a
copy of a built one there; nothing is recomputed. `BENCHGAP_DB=other.db` serves another file and
`BENCHGAP_PORT=9000` another port. Edits in `web/` show on reload.

The front-end needs no build step and no npm: the browser loads `web/assets/app.js`
and its modules (`web/assets/js/`, lit-html vendored as one file in `js/vendor/`)
as they are. `app.js` is the router; each page is a module of `js/pages/`, loaded
when the page is first opened, that draws the page as a lit-html template, so a
redraw (a filter, a sort, another benchmark) updates only what changed. The
deploy bundles them with esbuild: `app.js` with the code every page shares, and
a chunk per page under `assets/chunks/`, which `serve.php` preloads with the page.

The front-end never loads the whole data set: every page loads the shared
benchmark and model lists (`/data/site.json`) and only its own slice of the
scores (a leaderboard, a model, the matrix cells, one calibration; see
`web/src/Site.php`), and an estimate's details in the matrix load when its
tooltip opens. Each slice comes in the order the page shows it, with its
summaries already computed (the leaderboard highest first, the calibrations
lowest error first, the matrix's default row order and column ranges, the
cross-domain medians by capability pair); the browser only re-sorts what the
visitor picks, such as a matrix column. The server builds the site data once
per data update and keeps it until the next one.

Pages: per-benchmark leaderboards, the full score matrix, a page per
model, the calibration (predictability) matrix with a scatter + fitted
curve per mapping and the cross-domain predictability between capabilities,
the multivariate view (`/multivariate`: each benchmark from the others its
elastic net keeps, with its error, the error of the best of them alone, and
a measured vs. leave-one-out predicted scatter), the harness-tax page
(`/harness-tax`: how much harnesses disagree about the same models, measured
scores only), the methodology, and the
publications page (`/publications`: the papers on predicting benchmark
scores, and how benchgap relates to them). Each
page has its own URL
(`/matrix`, `/model/<slug>`, `/b/<name>/<version>`, ...) and is listed in the
generated `sitemap.xml`. The backend serves each page with its own title,
description and a plain-HTML summary of its content (`web/src/Pages.php`) for
readers without JavaScript, such as search and AI crawlers, and the same content
as Markdown in `llms.txt` and `llms-full.txt`.

Every estimate carries a **confidence level** computed in `web/src/Snapshot.php`:
high (LOO RMSE <= 5 pp), medium (<= 10 pp) or low (above), demoted
one level each for an extrapolated input, a mapping fitted on fewer than
8 models, or R2 < 0.5. Measured bars are solid; estimates are hatched and
italic with a +/- error whisker; low-confidence estimates get a dashed red
outline, a "low conf." flag on leaderboards and a red corner in the matrix.
Hovering any estimate shows its source score, curve and the reasons for its
level, and the "Show" toggle can hide low-confidence estimates or all of
them.

### Public API

The same data is published as a read-only JSON API (`web/src/Api.php`,
documented on the site at `/api` and in `api/v1/openapi.json`, OpenAPI 3.1),
with ETags and open CORS:

| Path | Returns |
| --- | --- |
| `index.json` | build metadata, endpoint templates, confidence thresholds |
| `benchmarks.json`, `benchmarks/{name}/{version}.json` | benchmark versions; one with all its scores |
| `models.json`, `models/{slug}.json` | models; one with all its scores |
| `scores.json`, `scores.csv` | every score, measured and estimated |
| `mappings.json`, `mappings/{id}.json` | calibrations; one with points and sampled curve |
| `openapi.json` | the OpenAPI 3.1 description of all of the above |

Benchmarks are addressed by `name/version` and models by slug (stable across
rebuilds). Scores are fractions; estimated scores carry `estimate` with
`confidence`, `error_pp`, `reasons` and the measured `inputs` they came from.

## Data model

- All fraction-scale scores are stored as fractions in `[0, 1]`; reports
  render percent. (Versions can declare other units; Elo-based benchmarks
  are stored but excluded from fitting.)
- Every benchmark declares a **capability** (agentic-terminal, agentic-tool,
  coding, math, knowledge, instruction-following, vision, ...). Mappings are
  only fitted between versions of the same capability - a model that was
  never run on a vision benchmark keeps that gap rather than inheriting a
  score from text benchmarks. `crossfit` fits the pairs across capabilities
  too, into `cross_mappings`, for the website's cross-domain predictability
  view only; no estimate comes from them. Neither do the multivariate
  view's fits in `cross_multi_mappings` (`crossmultifit`, after `crossfit`;
  see "Fitting and selection").
- `scores.source` is `measured` or `gapfilled`; gapfilled rows carry the
  `mapping_id` that produced them and a `prediction_json` with the input
  score, input version, method, and an extrapolation flag.
- `mappings` stores every fit per (from_version, to_version) pair: method,
  params, metrics (R2, adj R2, RMSE, LOO RMSE), training points, and the
  training x-range. `fit --keep all` stores all candidates, `fit` stores the
  best; gapfill always uses the mapping with the lowest LOO RMSE.
- `mapping_points` keeps the paired scores a mapping was fitted on, so fits
  are reproducible and auditable.

## Fitting and selection

Univariate candidates (see `fitting.py`): linear, Michaelis-Menten,
Michaelis-Menten with offset, the **inverse Michaelis-Menten form** (the
analytic inverse of the MM+offset curve, fitted by least squares in the
target space, for convex directions), **Hill** (generalizes MM+offset with
a cooperativity exponent; nests it at n=1), and an **offset logistic**. All
candidates are monotone; the quadratic is deliberately absent (a
non-monotone fit eventually predicts that a better model scores worse).

Multivariate gapfill (see `multivariate.py`, `benchgap multifit`): for each
target benchmark two searches run and the fit with the lower LOO CV error is
stored. One search fits one **elastic net** (`enet_mv`) over a pool of
same-capability source benchmarks and lets its lasso part decide the
features: the pool grows by data availability (candidates ranked by their
best single-source fit, then the models they share with the target; a
source joins only if the models measured on it and the pool so far still
clear the minimum training size, so a sparse candidate is skipped rather
than hiding the candidates ranked below it), and the penalty drives useless
sources' coefficients to
exactly zero. The other is the earlier greedy forward search: each step
tries every remaining candidate on its own training set. On whatever
features each lands on, two families compete by LOO CV: the linear elastic
net fit and a **multivariate
Michaelis-Menten** (`mm_mv`) that combines the sources into a weighted
aggregate capability index mapped through y = y0 + Vmax*s/(K + s),
monotone in every source. A multi-mapping is
stored only if the fit passes the quality gate and beats the target's best
univariate mapping - a fit the lasso leaves with a single source may be
stored too (its shrinkage can beat every univariate curve), while a
one-feature nonlinear fit is the univariate pipeline's job. Gapfill
prefers a multi-mapping when the model is measured on all its feature
benchmarks and its LOO RMSE is lower; otherwise the univariate path applies
- so a model missing one source benchmark simply falls back, and a
capability gap stays a gap.

The multivariate view (`benchgap crossmultifit`) runs the same search
for analysis only, with sources of any capability: from each target's 8 best
single predictors and the 8 benchmarks sharing the most models with it, the
lasso part must always keep two or more, and it stores every training
model's leave-one-out prediction for the page's scatter.

Selection is by leave-one-out CV RMSE (capped at 40 folds for large pairs),
which penalizes overfitting. A pair keeps a mapping only if the best fit
passes a quality gate (R2 >= 0.3 and LOO RMSE <= 15 pp) - weak pairs keep
no mapping, so their gaps stay gaps instead of being filled with noise.

Warnings the pipeline tracks: predictions outside the training x-range are
flagged `extrapolated` in `prediction_json` and in the `predict` CLI
output. Mapping coefficients are harness-specific: each benchmark version
records whose run it is (`artificial-analysis`, `vals-ai` or `published`).

## Harness tax

The same warning, measured: **how much do harnesses disagree about the same
models on the same benchmark?** `benchgap harness-tax` (`harness_tax.py`,
never used for estimates) pairs the benchmark versions that measure the same
benchmark under different harnesses or run protocols - the **families** of
`families.json` (seeded by hand, with a tier per family: `agentic` for
tool/scaffold-dependent benchmarks, `knowledge_tool_free` for tool-free ones,
`protocol_layer` for within-harness variants such as HLE tools on/off). Names
not covered by the seed are auto-detected by normalized base name
(`families.detect_families`) and enter as `candidate` families, shown but kept
out of aggregates until reviewed into the seed.

For each pair, the **models measured on both** versions give a per-model
delta in percentage points (score_a - score_b, in the registry's order), and
the metrics that separate a systematic harness tax from noise: mean/median/max
|delta|, the shares beyond 5 and 10 pp, Kendall tau-b of the two harnesses'
rankings, the number of rank flips, a two-sided exact sign test against
p = 0.5, and the directionality (the fraction agreeing with the dominant
sign; >= 0.9 is systematic bias). Pairs with fewer than 5 shared models are
stored but flagged `low_overlap` and greyed on the page; aggregates pool the
mean |delta| by tier over reportable pairs only (active families, enough
overlap, a known item set), with a verified-only headline set whose agentic
vs tool-free ratio is the analysis's headline number.

Everything is **measured scores only** - no estimate ever enters a delta, and
a paired version with no measured scores fails the run loudly. Every
per-model delta keeps both scores' retrieval dates and source URLs
(`/api/v1/harness-tax/{family_id}.json`); a |delta| beyond 20 pp lands in the
audit queue, as does any score outside [0, 1] (the pair is skipped). Each run
rewrites `data/harness_tax.json` and the `harness_tax_*` tables (which flow to
the site with `benchgap sql`) and **appends** one dated record per pair to
`data/harness_tax_history.jsonl` - history is never rewritten. The page is
`/harness-tax`; `uv run python scripts/export_paper.py` regenerates the
paper-ready exports (`exports/paper/`: Table 1's pairs CSV, Figure 1's
by-pair chart data, Figure 2's HLE tools effect, and the tier aggregates).

Limitations: pair counts are lower bounds (a model measured on only one side
cannot contribute), the item-set audit status is per family and partly manual
(`verified` / `needs_audit` / `weak_alignment`), and a family whose versions
changed items across harnesses (LiveCodeBench's v6, say) conflates version
and harness effects - which is why it is a candidate, out of the aggregates.

## Adding benchmarks

New BenchLM benchmarks arrive with the daily update; `scripts/build_seed.py`
sets each one's capability (`CAPABILITIES`) and which ones the leaderboard
picker shows first (`FEATURED`). Any CSV with the seed's columns can be
ingested too: `model_slug, model_name, release, benchmark, version, label,
featured, capability, unit, harness, score, source_url, retrieved_at`. Then
rerun `fit`, `multifit` and `gapfill` (and `crossfit`, `crossmultifit` for the
website's views): mappings are fitted automatically for every same-capability
version pair with at least 5 paired models.

## Probabilistic roadmap

The schema and code paths are laid out so probabilistic fitting can replace
the deterministic one without migration:

- `fitting.py` is a registry: a probabilistic method (e.g. Bayesian
  Michaelis-Menten via posterior sampling) registers a `fit` that returns
  params plus uncertainty, and a `predict` that returns draws.
- `mappings.params_json` can hold posterior summaries alongside point params.
- `scores.ci95_lo` / `scores.ci95_hi` are already in the schema; a
  probabilistic gapfill fills them and can store per-score posterior draws
  in `prediction_json`.
- Model selection can move from LOO-CV RMSE to out-of-sample log score once
  predictions are distributions.
- The multivariate mappings are the deterministic precursor of a joint
  model: a Bayesian network over benchmark scores would replace the
  per-target lasso feature selection and the per-direction calibrations with
  one coherent posterior, imputing every missing score with uncertainty.

## Sources

- [BenchLM.ai](https://benchlm.ai/data) (CC BY-NC 4.0, "Data from BenchLM.ai"):
  the measured scores of every model on every benchmark it tracks, including
  the runs of Artificial Analysis and Vals AI and published results; each
  benchmark's `source_url` points to its origin.

## License

The code is licensed under the [MIT License](LICENSE). The benchmark data is
from BenchLM.ai under CC BY-NC 4.0 and remains under its terms; the MIT
License covers only the code.
