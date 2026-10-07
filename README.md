# benchgap

A benchmark score database that gapfills missing scores. Models are measured
on some benchmarks but not others; benchgap fits a mapping between benchmark
versions from the models measured on both, then predicts the missing values.

Boostrapped from a prior analysis session that calibrated Terminal-Bench v4.0
scores onto the v2.1 scale and found that a Michaelis-Menten curve with offset

```
y = y0 + Vmax * x / (K + x)
```

fits far better (LOO-CV RMSE ~4 pp on the paired models) than linear or
quadratic alternatives. benchgap generalizes that to a multi-benchmark
database (24 Artificial Analysis evaluation leaderboards at seed time),
with mapping candidates fitted per version pair, model selection by
leave-one-out cross-validation, and gapfilled scores stored with full
provenance.

Deterministic least-squares today; probabilistic fitters later - see
"Probabilistic roadmap".

## Layout

```
├── data/
│   ├── raw/                 # source extracts from the original analysis session (provenance)
│   ├── aa_scores.json       # Artificial Analysis leaderboard snapshot (24 evaluations, 85 models)
│   ├── seed/scores.csv      # canonical long-format seed (scripts/build_seed.py)
│   └── benchgap.db          # generated SQLite database
├── scripts/build_seed.py    # regenerates data/seed/scores.csv from aa_scores.json
├── src/benchgap/
│   ├── db.py                # schema + data access (capability, unit)
│   ├── ingest.py            # seed CSV -> database
│   ├── fitting.py           # monotone mapping candidates, fit, LOO-CV, selection
│   ├── fit.py               # capability-aware mapping fits + quality gate
│   ├── gapfill.py           # predict + store missing scores (source='gapfilled')
│   ├── report.py            # mapping summary, score matrix
│   ├── html_report.py       # self-contained HTML report (matplotlib, base64 PNGs)
│   └── cli.py               # command-line interface
└── tests/
```

## Usage

```bash
uv venv && uv pip install -e ".[dev]"     # or: pip install -e ".[dev]"
benchgap init                              # create data/benchgap.db
benchgap ingest                            # load data/seed/scores.csv
benchgap fit -v                            # fit mappings (shows all candidates)
benchgap gapfill                           # fill missing scores
benchgap report                            # mapping summary + score matrix (terminal)
benchgap html                              # self-contained HTML report -> data/report.html
benchgap predict terminal-bench/4.0 terminal-bench/2.1 59.6
pytest                                      # run the test suite
```

The HTML report (`benchgap html`, needs the `report` extra: `pip install
".[report]"`) is a single self-contained file: database stats, mapping
summary, per-mapping fit figures (paired scores, all candidate curves,
residuals, gapfilled points marked), and the full score matrix with
gapfilled cells highlighted. Charts are embedded as base64 PNGs, so the
file works offline and can be shared as-is.

Run `benchgap` from the repository root (paths are relative to the working
directory); pass `--db` to use a different database file.

## Data model

- All fraction-scale scores are stored as fractions in `[0, 1]`; reports
  render percent. (Versions can declare other units; Elo-based benchmarks
  are stored but excluded from fitting.)
- Every benchmark declares a **capability** (agentic-terminal, agentic-tool,
  coding, math, knowledge, instruction-following, vision, ...). Mappings are
  only fitted between versions of the same capability - a model that was
  never run on a vision benchmark keeps that gap rather than inheriting a
  score from text benchmarks.
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

Candidates (see `fitting.py`): linear, Michaelis-Menten, Michaelis-Menten
with offset, and the **inverse Michaelis-Menten form** - the analytic
inverse of the MM+offset curve, fitted by least squares in the target
space. All candidates are monotone; the quadratic is deliberately absent (a
non-monotone fit eventually predicts that a better model scores worse). The
inverse form handles convex directions: a saturating curve read backwards
is convex, and inverting the forward fit's point predictions naively
amplifies error near the ceiling, so the inverse family is fitted directly.

Selection is by leave-one-out CV RMSE (capped at 40 folds for large pairs),
which penalizes overfitting. A pair keeps a mapping only if the best fit
passes a quality gate (R2 >= 0.3 and LOO RMSE <= 15 pp) - weak pairs keep
no mapping, so their gaps stay gaps instead of being filled with noise.

Warnings the pipeline tracks: predictions outside the training x-range are
flagged `extrapolated` in `prediction_json` and in the `predict` CLI
output. Mapping coefficients are harness-specific - the seed comes from the
Artificial Analysis harness, not the official leaderboards.

## Adding benchmarks

Append rows to `data/seed/scores.csv` (or ingest any CSV with the same
columns): `model_slug, model_name, benchmark, version, capability, unit,
harness, score, source_url, retrieved_at`. Then rerun `fit` and `gapfill` -
mappings are fitted automatically for every same-capability version pair
with at least 5 paired models. To refresh the Artificial Analysis snapshot,
re-fetch the evaluation pages listed in `scripts/build_seed.py` and update
`data/aa_scores.json`.

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

## Sources

- Artificial Analysis evaluation leaderboards
  (https://artificialanalysis.ai/evaluations/...), retrieved 2026-10-07:
  Terminal-Bench 2.1/4.0/Hard/Science, GPQA Diamond, MMLU-Pro,
  Global-MMLU-Lite, Humanity's Last Exam, CritPt, MATH-500, AIME 2025,
  LiveCodeBench, SciCode, tau-Bench (telecom + banking), AutomationBench,
  APEX-Agents, AA-AnalystAgent, EnterpriseOps-Gym, Harvey LAB, ITBench,
  IFBench, MMMU-Pro, GDP.pdf - 24 benchmark versions, 85 models, 396
  measured scores. Snapshot: `data/aa_scores.json`.
- Elo-based AA benchmarks (GDPval-AA, AA-Briefcase) and login-gated ones
  (Omniscience, CyberGym, MLCR) are excluded: their values are not in the
  public page content.
- Official Terminal-Bench leaderboards: https://www.tbench.ai/ (different
  harness; see data/raw/ for the original session's extracts).
