"""Tests for the website backend (web/serve.php) on the test database.

They need PHP with pdo_sqlite and web/vendor (composer install in web/);
without them they are skipped.
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from benchgap import fit, report
from benchgap.fitting import CANDIDATES

WEB = Path(__file__).resolve().parent.parent / "web"
PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(
    PHP is None or not (WEB / "vendor" / "autoload.php").exists(),
    reason="needs php and web/vendor (composer install in web/)",
)


@pytest.fixture(scope="module")
def server(gapfilled_db, tmp_path_factory):
    """PHP's built-in server running serve.php on the test database.

    It runs from a copy of web/ whose config.php points at the test database.
    """
    db_path = gapfilled_db.execute("PRAGMA database_list").fetchone()[2]
    web = tmp_path_factory.mktemp("web") / "web"
    shutil.copytree(WEB, web, ignore=shutil.ignore_patterns("vendor", "config.php"))
    (web / "vendor").symlink_to(WEB / "vendor")
    (web / "config.php").write_text(f"<?php return ['dsn' => 'sqlite:{db_path}'];\n")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [PHP, "-S", f"127.0.0.1:{port}", "-t", str(web), str(web / "serve.php")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/")
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait()


def get(server, path):
    with urllib.request.urlopen(server + path) as r:
        return r.headers, r.read().decode()


def get_json(server, path):
    return json.loads(get(server, path)[1])


def php(code: str) -> str:
    """Run PHP code with the backend's classes loaded."""
    return subprocess.run(
        [PHP, "-r", f"require '{WEB}/vendor/autoload.php'; {code}"],
        capture_output=True, text=True, check=True,
    ).stdout


def test_reliability_tiers():
    cases = php(
        "foreach ([[0.03, 0.9, 20, false], [0.07, 0.9, 20, false], [0.12, 0.9, 20, false],"
        " [0.03, 0.9, 20, true], [0.03, 0.9, 6, true], [0.03, 0.4, 20, false],"
        " [0.12, 0.2, 5, true], [null, 0.9, 20, false]] as $c)"
        " echo json_encode(Benchgap\\Snapshot::reliability(...$c)), \"\\n\";"
    ).splitlines()
    tiers = [json.loads(c)[0] for c in cases]
    # each warning demotes one tier; low is the floor; no error estimate is low
    assert tiers == ["high", "medium", "low", "medium", "low", "medium", "low", "low"]
    assert json.loads(cases[0])[1] == [] and len(json.loads(cases[6])[1]) == 4


def test_provider():
    names = ["Claude Opus 5.5 (max with fallback)", "GPT-6 Astra (high)", "o3", "Gemini 3 Flash", "Inkling (xhigh)"]
    out = php(f"foreach ({json.dumps(names)} as $n) echo Benchgap\\Snapshot::provider($n), ' ';")
    assert out.split() == ["anthropic", "openai", "openai", "google", "other"]


def test_settings_match_the_method():
    meta = json.loads(php(
        "echo json_encode(['gate' => Benchgap\\Snapshot::QUALITY_GATE, 'dense' => Benchgap\\Snapshot::DENSE,"
        " 'caps' => array_keys(Benchgap\\Snapshot::CAPABILITY_LABELS), 'curves' => array_keys(Benchgap\\Curves::EQUATIONS)]);"
    ))
    assert meta["gate"] == {"min_pairs": fit.MIN_PAIRS, "min_r2": fit.MIN_R2, "max_loo_pp": pytest.approx(fit.MAX_LOO_RMSE * 100)}
    assert meta["dense"] == {"min_models": report.DEFAULT_MIN_MODELS, "min_benchmarks": report.DEFAULT_MIN_BENCHMARKS}
    assert meta["caps"] == report.CAPABILITY_ORDER
    assert meta["curves"] == list(CANDIDATES)


def test_curves_match_python():
    """Curves.php plots and prints the same curves as fitting.py."""
    from benchgap.fitting import predict

    params = {
        "linear": {"slope": 0.8, "intercept": -0.05},
        "mm": {"vmax": 1.2, "k": 0.3},
        "mm_offset": {"y0": 0.1, "vmax": 0.9, "k": 0.4},
        "mm_offset_inv": {"y0": 0.05, "vmax": 0.8, "k": 0.6},
        "hill": {"y0": 0.02, "a": 0.95, "k": 0.4, "n": 2.5},
        "logistic": {"y0": 0.05, "a": 0.9, "k": 12.0, "xmid": 0.5},
    }
    xs = [i / 20 for i in range(21)]
    got = json.loads(php(
        f"$params = json_decode('{json.dumps(params)}', true); $out = [];"
        " foreach ($params as $m => $p) $out[$m] = ["
        f"  array_map(fn ($x) => Benchgap\\Curves::predict($m, $p, $x), {json.dumps(xs)}),"
        "  Benchgap\\Curves::equation($m, $p)];"
        " echo json_encode($out, JSON_UNESCAPED_UNICODE);"
    ))
    for method, p in params.items():
        ys, equation = got[method]
        assert ys == pytest.approx(predict(method, p, xs).tolist(), abs=1e-12), method
        assert equation == CANDIDATES[method].equation.format(**p), method


def test_site_data_matches_database(server, gapfilled_db):
    headers, body = get(server, "/data/benchgap.json")
    assert headers["Content-Type"].startswith("application/json")
    data = json.loads(body)
    c = data["meta"]["counts"]
    count = lambda source: gapfilled_db.execute(
        "SELECT COUNT(*) FROM scores WHERE source = ?", (source,)
    ).fetchone()[0]
    assert c["measured"] == count("measured")
    assert c["estimated"] == count("gapfilled") == sum(c["confidence"].values()) > 0
    assert c["confidence"]["high"] > 0 and c["confidence"]["low"] > 0
    dense_versions, dense_models = report.display_filter(gapfilled_db)
    assert {b["id"] for b in data["benchmarks"] if b["dense"]} == dense_versions
    assert {m["id"] for m in data["models"] if m["dense"]} == dense_models
    best = {m["id"] for m in report.best_mappings(gapfilled_db)}
    assert {m["id"] for m in data["mappings"]} == best
    for m in data["mappings"]:
        assert len(m["curve"]) >= 51 and m["equation"].startswith("y = ")


def test_api(server):
    index = get_json(server, "/api/v1/")
    assert index["api_version"] == "v1"
    benchmarks = get_json(server, "/api/v1/benchmarks.json")["benchmarks"]
    one = get_json(server, f"/api/v1/benchmarks/{benchmarks[0]['key']}.json")
    assert one["benchmark"]["key"] == benchmarks[0]["key"]
    assert [s["score"] for s in one["scores"]] == sorted((s["score"] for s in one["scores"]), reverse=True)
    model = get_json(server, "/api/v1/models.json")["models"][0]
    assert get_json(server, f"/api/v1/models/{model['slug']}.json")["model"] == model
    mapping = get_json(server, "/api/v1/mappings.json")["mappings"][0]
    detail = get_json(server, f"/api/v1/mappings/{mapping['id']}.json")
    assert detail["mapping"]["points"] and detail["mapping"]["curve"]
    headers, csv_text = get(server, "/api/v1/scores.csv")
    assert headers["Content-Type"].startswith("text/csv")
    assert len(csv_text.strip().splitlines()) == index["counts"]["measured"] + index["counts"]["estimated"] + 1
    for path in ["/api/v1/models/no-such-model.json", "/api/v1/mappings/999999.json", "/api/v1/nope"]:
        with pytest.raises(urllib.error.HTTPError) as err:
            get(server, path)
        assert err.value.code == 404 and err.value.headers["Content-Type"].startswith("application/json")


def test_revalidation(server):
    headers, _ = get(server, "/api/v1/models.json")
    assert headers["Access-Control-Allow-Origin"] == "*"
    request = urllib.request.Request(server + "/api/v1/models.json", headers={"If-None-Match": headers["ETag"]})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(request)
    assert err.value.code == 304


def test_pages_and_sitemap(server):
    # every page of the site is index.html at its own path, with its own head and content
    model = get_json(server, "/api/v1/models.json")["models"][0]
    mapping = get_json(server, "/api/v1/mappings.json")["mappings"][0]
    pages = {
        "/": "LLM Benchmark Leaderboard with Estimated Scores",
        "/matrix": "LLM benchmark score matrix",
        "/method": "How missing benchmark scores are estimated",
        "/api": "Public API",
        "/calibration": "LLM benchmark calibrations",
        f"/calibration/{mapping['id']}": "calibration",
        "/b/terminal-bench/2.1": "Terminal-Bench 2.1 leaderboard",
        f"/model/{model['slug']}": f"{model['name']} benchmark scores",
    }
    for path, title in pages.items():
        headers, body = get(server, path)
        assert headers["Content-Type"].startswith("text/html") and '<main id="main"' in body, path
        assert re.search(rf"<title>[^<]*{re.escape(title)} · benchgap</title>", body), path
        assert f'<link rel="canonical" href="https://benchgap.net{path}">' in body, path
        assert '<main id="main" class="wrap" tabindex="-1"><div class="page">' in body, path
    _, board = get(server, "/b/terminal-bench/2.1")
    assert "the highest measured score on Terminal-Bench 2.1 is" in board and f'href="/model/{model["slug"]}"' in get(server, "/matrix")[1]
    assert model["page"] == f"https://benchgap.net/model/{model['slug']}"
    for path in ["/b/no-such/benchmark", "/model/no-such-model", "/calibration/999999", "/no-such-page"]:
        with pytest.raises(urllib.error.HTTPError) as err:
            get(server, path)
        assert err.value.code == 404 and '<meta name="robots" content="noindex">' in err.value.read().decode()

    headers, sitemap = get(server, "/sitemap.xml")
    assert headers["Content-Type"].startswith("application/xml")
    index = get_json(server, "/api/v1/")
    n = index["counts"]
    assert sitemap.count("<loc>") == 5 + n["benchmarks"] + n["models"] + n["mappings"]
    assert f"<loc>{model['page']}</loc>" in sitemap and "<lastmod>" in sitemap


def test_llms_txt(server):
    headers, llms = get(server, "/llms.txt")
    assert headers["Content-Type"].startswith("text/markdown") and llms.startswith("# benchgap\n\n> benchgap is")
    benchmarks = get_json(server, "/api/v1/benchmarks.json")["benchmarks"]
    assert all(f"]({b['page']})" in llms for b in benchmarks)
    _, full = get(server, "/llms-full.txt")
    assert all(f"\n## {b['label']}\n" in full for b in benchmarks)
    scores = get_json(server, "/api/v1/scores.json")["scores"]
    assert full.count("\n| ") - full.count("\n| # |") == len(scores)
