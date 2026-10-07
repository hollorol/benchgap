# benchgap

A benchmark score database that gapfills missing scores. Models are measured
on some benchmarks but not others; benchgap fits a mapping between benchmark
versions from the models measured on both, then predicts the missing values.

Boostrapped from a prior analysis session that calibrated Terminal-Bench v4.0
scores onto the v2.1 scale and found that a Michaelis-Menten curve with offset

```
y = y0 + Vmax * x / (K + x)
```

fits far better (R2 = 0.954, LOO-CV RMSE = 3.5 pp on 20 paired models) than
linear or quadratic alternatives. benchgap generalizes that: any number of
benchmarks and versions, mapping candidates fitted per version pair, model
selection by leave-one-out cross-validation, and gapfilled scores stored with
full provenance (which mapping produced them, from which input score).

Deterministic least-squares today; probabilistic fitters later - see
"Probabilistic roadmap".

## Layout

```
├── data/
│   ├── raw/                 # source extracts (Artificial Analysis harness), provenance
│   ├── seed/scores.csv      # canonical long-format seed (built by scripts/build_seed.py)
│   └── benchgap.db          # generated SQLite database
├── scripts/build_seed.py    # regenerates data/seed/scores.csv from data/raw
├── src/benchgap/
│   ├── db.py                # schema + data access
│   ├── ingest.py            # seed CSV -> database
│   ├── fitting.py           # mapping candidates, fit, LOO-CV, selection
│   ├── fit.py               # fit mappings for all version pairs with paired data
│   ├── gapfill.py           # predict + store missing scores (source='gapfilled')
│   ├── report.py            # mapping summary, score matrix
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
benchgap report                            # mapping summary + score matrix
benchgap predict terminal-bench/4.0 terminal-bench/2.1 59.6
pytest                                      # run the test suite
```

Run `benchgap` from the repository root (paths are relative to the working
directory); pass `--db` to use a different database file.

## Data model

- All scores are stored as fractions in `[0, 1]`; reports render percent.
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

Candidates (see `fitting.py`): linear, quadratic, Michaelis-Menten, and
Michaelis-Menten with offset. Selection is by leave-one-out CV RMSE, which
penalizes overfitting; on the seed data this picks `mm_offset` in both
directions. The MM+offset form is monotone with a ceiling, so predictions stay
physical (a better v4 score never maps to a worse v2 score) where a quadratic
eventually turns down.

Warnings the pipeline tracks: predictions outside the training x-range are
flagged `extrapolated` in `prediction_json` and in the `predict` CLI output.
Mapping coefficients are harness-specific - the seed pairs come from the
Artificial Analysis harness, not the official tbench.ai leaderboards, so
calibrating official scores needs pairs measured on the official harness.

## Adding benchmarks

Append rows to `data/seed/scores.csv` (or ingest any CSV with the same
columns): `model_slug, model_name, release, benchmark, version, harness,
score, source_url, retrieved_at`. Then rerun `fit` and `gapfill` - mappings
are fitted automatically for every version pair with at least 5 paired
models.

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

- Artificial Analysis, Terminal-Bench 2.1 and 4.0 evaluations
  (https://artificialanalysis.ai/evaluations/terminalbench-2-1,
  https://artificialanalysis.ai/evaluations/terminalbench-4-0)
- Official leaderboards: https://www.tbench.ai/
- Raw extracts and the original analysis artifacts live in `data/raw/`.
