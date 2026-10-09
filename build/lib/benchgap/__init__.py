"""benchgap: benchmark score database with gapfilling of missing scores.

Scores are stored per model and benchmark version. Where a model has a
measured score on one version but not another, a fitted mapping between the
two versions (selected by leave-one-out CV) predicts the missing value. The
fitted mappings are deterministic least-squares fits today; the schema and
the mapping registry are designed so probabilistic fitters can be added
without migration.
"""

__version__ = "0.1.0"
