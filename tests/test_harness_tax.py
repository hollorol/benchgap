"""The harness-tax analysis: pairing, metrics, the audit trail and the reference run.

The unit tests run on tiny synthetic databases with hand-computable numbers; the
golden test (test_reference_run) checks the 2026-10-08 manual analysis's values
against this machine's data/benchgap.db and skips itself where that file is absent
(CI): the reference counts are lower bounds (the manual run read the size-capped
public API), so overlap may only have grown, and a pair whose overlap has not
drifted must reproduce its mean |delta| and tau.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchgap import harness_tax
from benchgap.db import (
    connect,
    get_or_create_model,
    get_or_create_version,
    init_db,
    set_measured_score,
)
from benchgap.families import detect_families, load_families, normalize_name, validate

REAL_DB = Path("data") / "benchgap.db"


def make_db(tmp_path, families):
    """A database with the given registry families' versions and their models' scores.

    families: registry dicts (families.json's shape) whose versions carry an extra
    'scores' key: {model_slug: score, ...}. Returns the connection.
    """
    conn = connect(tmp_path / "ht.db")
    init_db(conn)
    for f in families:
        for v in f["versions"]:
            vid = get_or_create_version(conn, v["name"], "current", v.get("harness") or "unknown")
            for slug, score in (v.get("scores") or {}).items():
                set_measured_score(conn, get_or_create_model(conn, slug, slug), vid, score, "2026-10-08")
    conn.commit()
    return conn


def family(name, capability, tier, versions, same_item_set="verified", **kw):
    versions = [{"name": n, "harness": h, "scores": s} for n, h, s in versions]
    return {"family_id": name, "label": name, "capability": capability, "tier": tier,
            "same_item_set": same_item_set, "status": "active", "versions": versions, **kw}


# --- the metrics, on hand-computable numbers ------------------------------------


def test_metrics_known_tau_and_sign_test():
    # scores a: [0.9, 0.8, 0.7, 0.6], b: [0.9, 0.75, 0.65, 0.3]: all pairs concordant -> tau-b = 1
    deltas = [{"model_id": i, "score_a": a, "score_b": b, "delta_pp": (a - b) * 100}
              for i, (a, b) in enumerate(zip([.9, .8, .7, .6], [.9, .75, .65, .3]))]
    m = harness_tax.pair_metrics(deltas)
    assert m["kendall_tau"] == pytest.approx(1.0)
    assert m["n_rank_flips"] == 0
    assert m["mean_abs_pp"] == pytest.approx((0 + 5 + 5 + 30) / 4)
    assert m["median_abs_pp"] == pytest.approx(5.0)
    assert m["share_gt_5"] == pytest.approx(0.5)
    assert m["share_gt_10"] == pytest.approx(0.25)
    assert m["n_positive"] == 3 and m["n_negative"] == 0
    assert m["sign_p"] == pytest.approx(0.25)          # two-sided binomial with k=0, n=3
    assert m["directionality"] == 1.0


def test_metrics_rank_flips_and_ties():
    # three discordant pairs (model 0 below everyone else on b) and one tie on b
    a, b = [.9, .8, .7, .6], [.5, .7, .7, .6]
    deltas = [{"model_id": i, "score_a": x, "score_b": y, "delta_pp": (x - y) * 100}
              for i, (x, y) in enumerate(zip(a, b))]
    m = harness_tax.pair_metrics(deltas)
    from scipy import stats
    assert m["n_rank_flips"] == 3                      # model 0: above all on a, below 1, 2 and 3 on b
    assert m["kendall_tau"] == pytest.approx(stats.kendalltau(a, b).statistic)
    assert m["n_positive"] == 2 and m["n_negative"] == 0 and m["directionality"] == 1.0
    assert m["sign_p"] == pytest.approx(0.5)           # two-sided binomial with k=0, n=2


def test_metrics_zero_overlap_is_a_row_of_nulls():
    m = harness_tax.pair_metrics([])
    assert m["n"] == 0 and m["mean_abs_pp"] is None and m["kendall_tau"] is None


# --- the registry ----------------------------------------------------------------


@pytest.mark.parametrize("name,base", [
    ("aa-terminal-bench21", "terminalbench21"),
    ("vals-terminal-bench21", "terminalbench21"),
    ("terminalbench21", "terminalbench21"),
    ("terminal-bench-4", "terminalbench4"),
    ("aa-terminal-bench4", "terminalbench4"),
    ("livecodebench-v6", "livecodebench"),
    ("livecodebench-pro", "livecodebench"),
    ("valsswebench", "swebench"),
    ("mmlu-pro-arcee", "mmlu"),
    ("hlenotools", "hle"),
    ("hlewithtools", "hle"),
])
def test_normalize_name(name, base):
    assert normalize_name(name) == base


def test_registry_validates():
    families = load_families()
    validate(families)                                 # the packaged seed is valid
    with pytest.raises(ValueError):
        validate(families + families[:1])              # duplicate family_id
    with pytest.raises(ValueError):
        validate([{**families[0], "status": "retired"}])
    with pytest.raises(ValueError):
        validate([{**families[0], "same_item_set": "unknown"}])
    with pytest.raises(ValueError):
        validate([{**families[0], "versions": [{"name": "a"}]}])


def test_auto_detection_finds_only_uncovered_groups(tmp_path):
    seed = load_families()
    fams = [
        family("foo", "general", None,
               [("aa-foo", "artificial-analysis", {"m1": 0.5, "m2": 0.6}),
                ("foo", "published", {"m1": 0.6, "m2": 0.7})]),
        family("gpqa", "knowledge", "knowledge_tool_free",
               [("gpqa-diamond", "published", {"m1": 0.5}),
                ("aa-gpqa-diamond", "artificial-analysis", {"m1": 0.5})]),
    ]
    conn = make_db(tmp_path, fams)
    candidates = detect_families(conn, seed)
    # gpqa-diamond is covered by the seed; only aa-foo/foo is new
    assert [c["family_id"] for c in candidates] == ["foo"]
    assert candidates[0]["status"] == "candidate" and candidates[0]["origin"] == "auto"


# --- pairing, deltas, audit --------------------------------------------------------


def test_measured_only_and_deltas(tmp_path):
    """No estimate ever enters a delta, and the delta sign follows the registry order."""
    scores_a = {"m1": 0.8, "m2": 0.6, "m3": 0.7}
    scores_b = {"m1": 0.5, "m2": 0.5, "m3": 0.9}
    fams = [family("fam", "knowledge", "knowledge_tool_free",
                   [("bench-pub", "published", scores_a),
                    ("bench-aa", "artificial-analysis", scores_b)])]
    conn = make_db(tmp_path, fams)
    model_id = lambda slug: conn.execute("SELECT id FROM models WHERE slug = ?", (slug,)).fetchone()["id"]
    # a gapfilled score where a measured one is missing must not enter the analysis
    conn.execute(
        "INSERT INTO scores (model_id, version_id, value, source) VALUES (?, ?, 0.5, 'gapfilled')",
        (get_or_create_model(conn, "m4", "m4"),
         conn.execute("SELECT v.id FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id"
                       " WHERE b.name = 'bench-aa'").fetchone()["id"]),
    )
    conn.commit()
    analysis = harness_tax.analyze(conn, families=fams)
    pair = analysis["pairs"][0]
    assert pair["a"]["name"] == "bench-pub" and pair["b"]["name"] == "bench-aa"
    assert {d["model_id"] for d in pair["deltas"]} == {model_id(s) for s in scores_a}
    by = {round(d["delta_pp"]): d for d in pair["deltas"]}
    assert 30 in by and -20 in by and by[-20]["score_a"] == 0.7
    assert pair["metrics"]["mean_abs_pp"] == pytest.approx(20.0)    # |30| + |10| + |−20| over 3 models
    assert analysis["measured_only"] is True


def test_low_overlap_flag_and_aggregates(tmp_path):
    """Pairs under the reporting minimum are kept but stay out of the aggregates."""
    fams = [family("small", "knowledge", "knowledge_tool_free",
                   [("s1", "published", {f"m{i}": 0.5 + 0.01 * i for i in range(4)}),
                    ("s2", "vals-ai", {f"m{i}": 0.4 + 0.01 * i for i in range(4)})],
                   same_item_set="needs_audit")]
    conn = make_db(tmp_path, fams)
    analysis = harness_tax.analyze(conn, families=fams)
    pair = analysis["pairs"][0]
    assert pair["low_overlap"] is True                       # 4 < the default 5
    assert analysis["aggregates"]["by_tier"] == {}           # nothing reportable
    assert analysis["aggregates"]["headline"]["ratio"] is None


def test_headline_ratio_and_weak_alignment(tmp_path):
    """The headline ratio pools verified families only; weak alignment stays out everywhere."""
    fams = [
        family("tb", "agentic-terminal", "agentic",
               [("tb-pub", "published", {f"m{i}": 0.9 - 0.1 * i for i in range(6)}),
                ("tb-vals", "vals-ai", {f"m{i}": 0.5 - 0.1 * i for i in range(6)})]),
        family("kp", "knowledge", "knowledge_tool_free",
               [("kp-pub", "published", {f"m{i}": 0.5 + 0.01 * i for i in range(6)}),
                ("kp-aa", "artificial-analysis", {f"m{i}": 0.5 for i in range(6)})]),
        family("weak", "coding", "agentic",
               [("w-pub", "published", {f"m{i}": 0.9 - 0.1 * i for i in range(6)}),
                ("w-vals", "vals-ai", {f"m{i}": 0.5 - 0.1 * i for i in range(6)})],
               same_item_set="weak_alignment"),
    ]
    conn = make_db(tmp_path, fams)
    analysis = harness_tax.analyze(conn, families=fams)
    agg = analysis["aggregates"]
    # tb: every model 0.4 apart (40 pp); kp: deltas 0..5 pp, mean 2.5 pp; weak is excluded everywhere
    assert agg["by_tier"]["agentic"] == {"n_pairs": 1, "n_model_pairs": 6, "pooled_mean_abs_pp": 40.0}
    assert agg["by_tier"]["knowledge_tool_free"]["pooled_mean_abs_pp"] == pytest.approx(2.5)
    assert "protocol_layer" not in agg["by_tier"]      # no reportable protocol pairs
    assert agg["headline"]["ratio"] == pytest.approx(40.0 / 2.5)
    assert agg["by_capability"]["knowledge"]["n_pairs"] == 1


def test_empty_measured_fails_loudly(tmp_path):
    fams = [family("fam", "knowledge", "knowledge_tool_free",
                   [("s1", "published", {"m1": 0.5}), ("s2", "vals-ai", {})])]
    conn = make_db(tmp_path, fams)
    with pytest.raises(harness_tax.HarnessTaxError):
        harness_tax.analyze(conn, families=fams)


def test_scale_sanity_skips_the_pair(tmp_path):
    fams = [family("fam", "knowledge", "knowledge_tool_free",
                   [("s1", "published", {"m1": 0.5, "m2": 0.6}),
                    ("s2", "vals-ai", {"m1": 0.4, "m2": 1.5})])]
    conn = make_db(tmp_path, fams)
    analysis = harness_tax.analyze(conn, families=fams)
    assert analysis["pairs"] == []
    assert any("outside [0, 1]" in w for w in analysis["audit"]["warnings"])


def test_outlier_queue_and_store(tmp_path):
    fams = [family("fam", "knowledge", "knowledge_tool_free",
                   [("s1", "published", {"m1": 0.9, "m2": 0.6, "m3": 0.5, "m4": 0.5, "m5": 0.5}),
                    ("s2", "vals-ai", {"m1": 0.5, "m2": 0.6, "m3": 0.5, "m4": 0.5, "m5": 0.5})])]
    conn = make_db(tmp_path, fams)
    analysis = harness_tax.analyze(conn, families=fams)
    m1 = conn.execute("SELECT id FROM models WHERE slug = 'm1'").fetchone()["id"]
    assert [(o["model_id"], round(o["delta_pp"])) for o in analysis["audit"]["outliers"]] == [(m1, 40)]
    harness_tax.store(conn, analysis)
    assert conn.execute("SELECT COUNT(*) FROM harness_tax_pairs").fetchone()[0] == 1
    row = conn.execute("SELECT n_positive, n_negative, mean_abs_pp FROM harness_tax_pairs").fetchone()
    assert tuple(row) == (1, 0, 8.0)                   # one +40 pp delta, four zeros
    assert conn.execute("SELECT COUNT(*) FROM harness_tax_deltas").fetchone()[0] == 5
    stored = json.loads(conn.execute("SELECT aggregates_json FROM harness_tax_aggregates").fetchone()[0])
    assert stored["by_tier"]["knowledge_tool_free"]["pooled_mean_abs_pp"] == 8.0
    assert stored["headline"]["ratio"] is None           # no reportable agentic pair, so no ratio


def test_history_appends_per_run(tmp_path):
    fams = [family("fam", "knowledge", "knowledge_tool_free",
                   [("s1", "published", {f"m{i}": 0.5 + 0.1 * i for i in range(6)}),
                    ("s2", "vals-ai", {f"m{i}": 0.5 for i in range(6)})])]
    conn = make_db(tmp_path, fams)
    history = tmp_path / "history.jsonl"
    harness_tax.run(conn, families=fams, output=tmp_path / "ht.json", history=history)
    harness_tax.run(conn, families=fams, output=tmp_path / "ht.json", history=history)
    lines = [json.loads(line) for line in history.read_text().splitlines()]
    assert len(lines) == 2                            # one record per pair per run
    assert lines[0]["pair"] == lines[1]["pair"] == "s1 vs s2"
    assert lines[0]["n"] == lines[1]["n"] == 6


# --- the golden test against the 2026-10-08 manual run ------------------------------

# the manual analysis of the public API, 2026-10-08: pair counts are lower bounds
# (responses were size-capped), so a pair is checked tightly only where the overlap
# has not grown since (data_retrieved drifts daily)
REFERENCE = [
    ("terminal-bench-2-1", "terminalbench21", "vals-terminal-bench21", 29, 12.1, 0.512),
    ("terminal-bench-2-1", "vals-terminal-bench21", "aa-terminal-bench21", 11, 9.8, 0.873),
    ("terminal-bench-2-1", "terminalbench21", "aa-terminal-bench21", 10, 3.3, 0.889),
    ("terminal-bench-4-0", "terminal-bench-4", "aa-terminal-bench4", 11, 3.9, 0.818),
    ("gpqa-diamond", "gpqa-diamond", "vals-gpqa-diamond", 23, 1.4, 0.684),
    ("mmmu-pro", "mmmu-pro", "aa-mmmu-pro", 32, 2.1, 0.671),
    ("livecodebench", "livecodebench-v6", "valslivecodebench", 4, 3.3, -0.167),
]


@pytest.mark.skipif(not REAL_DB.exists(), reason="no local benchgap.db")
def test_reference_run():
    conn = connect(REAL_DB, readonly=True)
    analysis = harness_tax.analyze(conn)
    by_pair = {(p["family_id"], p["a"]["name"], p["b"]["name"]): p for p in analysis["pairs"]}
    for family_id, a, b, ref_n, ref_mean, ref_tau in REFERENCE:
        p = by_pair[(family_id, a, b)]
        assert p["metrics"]["n"] >= ref_n, (family_id, a, b)
        if p["metrics"]["n"] == ref_n:
            assert abs(p["metrics"]["mean_abs_pp"] - ref_mean) <= 1.5, (family_id, a, b)
            assert abs(p["metrics"]["kendall_tau"] - ref_tau) <= 0.05, (family_id, a, b)
