"""Tests for the website backend (web/serve.php) on the test database.

They need PHP (with PDO SQLite, for the test database) and web/vendor (composer install in web/);
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
    # chunks as the bundled site has them (deploy.yml): shared code, and a page's own
    (web / "assets" / "chunks").mkdir()
    for chunk in ["chunk-SHARED.js", "board-PAGE.js"]:
        (web / "assets" / "chunks" / chunk).write_text("")
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


def test_front_end_modules_name_each_function_once():
    # a second declaration of a function inside another one silently replaces the first (they are
    # hoisted), which breaks whatever used the first; the front-end is app.js and its modules (js/)
    for path in [WEB / "assets" / "app.js", *(WEB / "assets" / "js").rglob("*.js")]:
        if "vendor" in path.parts:
            continue
        names = re.findall(r"^\s*function (\w+)\(", path.read_text(), re.M)
        assert not {n for n in names if names.count(n) > 1}, path


def test_backend_sql_runs_on_mysql():
    # the tests run the backend on SQLite, benchgap.net on MySQL: SQL that only SQLite knows
    # passes here and breaks every page there (COLLATE NOCASE did)
    sqlite_only = re.compile(r"COLLATE\s+NOCASE", re.I)
    for path in [WEB / "serve.php", *(WEB / "src").glob("*.php")]:
        assert not sqlite_only.search(path.read_text()), path


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


def test_site_data_matches_database(server, gapfilled_db, writable_db):
    headers, body = get(server, "/data/benchgap.json")
    assert headers["Content-Type"].startswith("application/json")
    data = json.loads(body)
    c = data["meta"]["counts"]
    # the site lists the benchmarks with an estimate and enough models; its counts are of those
    listed = {b["id"] for b in data["benchmarks"] if b["listed"]}
    assert listed and listed == {
        b["id"] for b in data["benchmarks"] if b["n_estimated"] >= 1 and b["n_measured"] + b["n_estimated"] >= 10
    }
    assert c["benchmarks"] == len(listed)
    marks = ",".join("?" * len(listed))
    count = lambda source: gapfilled_db.execute(
        f"SELECT COUNT(*) FROM scores WHERE source = ? AND version_id IN ({marks})", (source, *listed)
    ).fetchone()[0]
    assert c["measured"] == count("measured")
    assert c["estimated"] == count("gapfilled") == sum(c["confidence"].values()) > 0
    assert c["confidence"]["high"] > 0 and c["confidence"]["low"] > 0
    # the dense core is the display filter's, over the listed benchmarks' scores
    writable_db.execute(f"DELETE FROM scores WHERE version_id NOT IN ({marks})", tuple(listed))
    dense_versions, dense_models = report.display_filter(writable_db)
    assert {b["id"] for b in data["benchmarks"] if b["dense"]} == dense_versions
    assert {m["id"] for m in data["models"] if m["dense"]} == dense_models
    best = {m["id"] for m in report.best_mappings(gapfilled_db)}
    assert {m["id"] for m in data["mappings"]} == best
    for m in data["mappings"]:
        assert len(m["curve"]) >= 51 and m["equation"].startswith("y = ")


def test_page_data_slices_the_site_data(server):
    """Each page's data (data/..., Site.php) is its slice of the whole site data, in the order
    the page shows it (orders that do not depend on the visitor are made on the server)."""
    whole = get_json(server, "/data/benchgap.json")
    site = get_json(server, "/data/site.json")
    for k in ("capabilities", "benchmarks", "models"):
        assert site[k] == whole[k]
    assert [m["name"].lower() for m in whole["models"]] == sorted(m["name"].lower() for m in whole["models"])
    listed = {b["id"] for b in whole["benchmarks"] if b["listed"]}
    highest_first = lambda scores: sorted(scores, key=lambda s: -s["v"])  # noqa: E731 (stable: ties keep their order)

    # the leaderboard picker: each capability's listed benchmarks, the featured ones first (in the
    # site's order), then the most measured
    for cap, ids in site["picker"].items():
        benches = [b for b in whole["benchmarks"] if b["listed"] and b["capability"] == cap]
        rest = sorted((b for b in benches if not b["featured"]), key=lambda b: (-b["n_measured"], b["label"].lower()))
        assert ids == [b["id"] for b in benches if b["featured"]] + [b["id"] for b in rest]
    assert sum(map(len, site["picker"].values())) == len(listed)
    home = site["meta"]["home"]
    assert home in {b["key"] for b in whole["benchmarks"] if b["listed"]}
    scores = whole["scores"]

    bench = next(b for b in whole["benchmarks"] if b["key"] == home)
    board = get_json(server, f"/data/b/{home}.json")
    assert board == {"benchmark": bench["id"], "scores": highest_first(s for s in scores if s["b"] == bench["id"])}
    assert get_json(server, "/data/home.json") == board
    model = whole["models"][0]
    assert get_json(server, f"/data/model/{model['slug']}.json")["scores"] == [
        s for s in scores if s["m"] == model["id"] and s["b"] in listed
    ]

    kinds = {"high": 1, "medium": 2, "low": 3}
    matrix = get_json(server, "/data/matrix.json")
    cells = matrix["cells"]
    assert cells == [[s["m"], s["b"], round(s["v"], 4), 0 if s["s"] == "m" else kinds[s["tier"]]] for s in scores if s["b"] in listed]
    # each column's measured range, and the rows most measured first
    for b in listed:
        vals = [c[2] for c in cells if c[1] == b and c[3] == 0]
        assert matrix["range"].get(str(b)) == ([min(vals), max(vals)] if vals else None)
    rows = sorted((m for m in whole["models"] if m["listed"]), key=lambda m: -m["n_measured"])  # ties: by name, as whole
    assert matrix["models"] == [m["id"] for m in rows]
    est = next(s for s in scores if s["s"] == "e")
    assert get_json(server, f"/data/score/{est['m']}/{est['b']}.json") == est

    maps = get_json(server, "/data/calibration.json")["mappings"]
    # the best first
    assert [m["id"] for m in maps] == [m["id"] for m in sorted(
        (m for m in whole["mappings"] if m["from"] in listed and m["to"] in listed), key=lambda m: m["loo"])]
    assert "points" not in maps[0] and "curve" not in maps[0]
    one = get_json(server, f"/data/calibration/{maps[0]['id']}.json")
    assert one["mapping"] == next(m for m in whole["mappings"] if m["id"] == maps[0]["id"])
    assert one["estimates"] == highest_first(s for s in scores if s["s"] == "e" and s["via"]["kind"] == "uni" and s["via"]["mapping"] == maps[0]["id"])

    # the cross-domain fits: compact, between listed benchmarks
    cross_doc = get_json(server, "/data/cross.json")
    cross = cross_doc["cross"]
    assert cross and cross == [c for c in whole["cross_mappings"] if c[0] in listed and c[1] in listed]
    # ... summed up by capability pair: the median and the best of those that pass the gate
    cap = {b["id"]: b["capability"] for b in whole["benchmarks"]}
    by_caps = {}
    for c in cross:
        by_caps.setdefault(f"{cap[c[0]]}:{cap[c[1]]}", []).append(c)
    assert set(cross_doc["summary"]) == set(by_caps)
    for caps, rows in by_caps.items():
        ok = sorted(([c[0], c[1], c[5]] for c in rows if c[6] and c[5] is not None), key=lambda c: c[2])
        assert cross_doc["summary"][caps] == {
            "n": len(rows), "n_pass": len(ok), "median": ok[len(ok) // 2][2] if ok else None, "best": ok[0] if ok else None}
    assert whole["meta"]["counts"]["cross_mappings"] == len(whole["cross_mappings"])

    # the multivariate view's fits: of listed benchmarks from listed ones, each model's prediction
    multi = get_json(server, "/data/multivariate.json")["multivariate"]
    assert multi and multi == [m for m in whole["cross_multi_mappings"] if m["to"] in listed and set(m["from"]) <= listed]
    assert all(len(m["from"]) >= 2 and len(m["points"]) == m["n"] for m in multi)
    assert whole["meta"]["counts"]["cross_multi_mappings"] == len(whole["cross_multi_mappings"])

    # the API page's picker lists: the calibrations (as calibration.json, best first) and the harness-tax families
    api = get_json(server, "/data/api.json")
    assert api["mappings"] == [{k: m[k] for k in ("id", "from", "to", "loo")} for m in maps]
    assert api["families"] == [{k: f[k] for k in ("family_id", "label", "capability")} for f in whole["harness_tax"]["families"]]

    # the harness-tax pairs, the biggest disagreement first (no mean: last)
    means = [-1.0 if p["mean_abs_pp"] is None else p["mean_abs_pp"] for p in whole["harness_tax"]["pairs"]]
    assert means == sorted(means, reverse=True)

    for path in ["/data/b/no-such/bench.json", "/data/model/no-such-model.json", "/data/calibration/999999.json", "/data/score/0/0.json"]:
        with pytest.raises(urllib.error.HTTPError) as err:
            get(server, path)
        assert err.value.code == 404 and err.value.headers["Content-Type"].startswith("application/json")


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
    # the API serves every score, listed benchmark or not
    assert len(csv_text.strip().splitlines()) == len(get_json(server, "/api/v1/scores.json")["scores"]) + 1
    for path in ["/api/v1/models/no-such-model.json", "/api/v1/mappings/999999.json", "/api/v1/nope"]:
        with pytest.raises(urllib.error.HTTPError) as err:
            get(server, path)
        assert err.value.code == 404 and err.value.headers["Content-Type"].startswith("application/json")


def test_api_is_described_everywhere(server):
    """The endpoints are the same in index.json, openapi.json and the API page (js/pages/api.js API_ENDPOINTS),
    and every object has exactly the fields its openapi schema describes."""
    spec = json.loads((WEB / "api" / "v1" / "openapi.json").read_text())
    paths = {p.lstrip("/") for p in spec["paths"]}
    assert set(get_json(server, "/api/v1/")["endpoints"].values()) | {"index.json"} == paths
    page = re.search(r"const API_ENDPOINTS = \[(.*?)\n\];", (WEB / "assets" / "js" / "pages" / "api.js").read_text(), re.S).group(1)
    assert set(re.findall(r'^  \["([^"]+)"', page, re.M)) == paths

    schemas = spec["components"]["schemas"]

    def fields(name):
        schema = schemas[name]
        out = set(schema.get("properties", {}))
        for part in schema.get("allOf", []):
            out |= fields(part["$ref"].rsplit("/", 1)[1]) if "$ref" in part else set(part.get("properties", {}))
        return out

    benchmark = get_json(server, "/api/v1/benchmarks.json")["benchmarks"][0]
    assert set(benchmark) == fields("Benchmark")
    assert set(get_json(server, "/api/v1/models.json")["models"][0]) == fields("Model")
    scores = get_json(server, "/api/v1/scores.json")["scores"]
    assert all(set(s) == fields("Score") for s in scores)
    assert all(set(s["estimate"]) == fields("Estimate") for s in scores if s["estimate"])
    mapping = get_json(server, "/api/v1/mappings.json")["mappings"][0]
    assert set(mapping) == fields("Mapping")
    assert set(get_json(server, f"/api/v1/mappings/{mapping['id']}.json")["mapping"]) == fields("MappingDetail")
    assert set(get_json(server, "/api/v1/index.json")) == fields("About") | fields("Index")


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
        "/multivariate": "Multivariate LLM benchmark predictions",
        "/harness-tax": "The harness tax: how much harnesses disagree",
        "/publications": "Publications: the research behind benchgap",
        f"/calibration/{mapping['id']}": "calibration",
        "/b/aa-terminal-bench21/current": "AA Terminal-Bench 2.1 leaderboard",
        f"/model/{model['slug']}": f"{model['name']} benchmark scores",
    }
    for path, title in pages.items():
        headers, body = get(server, path)
        assert headers["Content-Type"].startswith("text/html") and '<main id="main"' in body, path
        assert re.search(rf"<title>[^<]*{re.escape(title)} · benchgap</title>", body), path
        assert f'<link rel="canonical" href="https://benchgap.net{path}">' in body, path
        assert '<main id="main" class="wrap" tabindex="-1"><div class="page">' in body, path
        # the data app.js renders the page from loads alongside app.js; app.js and style.css by their content
        data = re.findall(r'<link rel="preload" href="(/data/[^"]+)" as="fetch"', body)
        assert data[0] == "/data/site.json" and len(data) == (1 if path in ("/method", "/publications") else 2), path
        assert all(get_json(server, url) for url in data), path
        assert re.search(r'<script type="module" src="/assets/app\.js\?v=[0-9a-f]+">', body), path
        # on the bundled site, the shared chunks and the page's own load alongside app.js
        chunks = re.findall(r'<link rel="modulepreload" href="/assets/chunks/([^"]+)">', body)
        assert chunks == ["chunk-SHARED.js", *(["board-PAGE.js"] if path == "/" or path.startswith("/b/") else [])], path
        assert re.search(r'<link rel="stylesheet" href="/assets/style\.css\?v=[0-9a-f]+">', body), path
    _, board = get(server, "/b/aa-terminal-bench21/current")
    assert "the highest measured score on AA Terminal-Bench 2.1 is" in board and f'href="/model/{model["slug"]}"' in get(server, "/matrix")[1]
    assert model["page"] == f"https://benchgap.net/model/{model['slug']}"
    for path in ["/b/no-such/benchmark", "/model/no-such-model", "/calibration/999999", "/no-such-page"]:
        with pytest.raises(urllib.error.HTTPError) as err:
            get(server, path)
        assert err.value.code == 404 and '<meta name="robots" content="noindex">' in err.value.read().decode()

    headers, sitemap = get(server, "/sitemap.xml")
    assert headers["Content-Type"].startswith("application/xml")
    index = get_json(server, "/api/v1/")
    n = index["counts"]
    assert sitemap.count("<loc>") == 8 + n["benchmarks"] + n["models"] + n["mappings"]
    assert f"<loc>{model['page']}</loc>" in sitemap and "<lastmod>" in sitemap


def test_llms_txt(server):
    headers, llms = get(server, "/llms.txt")
    assert headers["Content-Type"].startswith("text/markdown") and llms.startswith("# benchgap\n\n> benchgap is")
    benchmarks = [b for b in get_json(server, "/api/v1/benchmarks.json")["benchmarks"] if b["listed"]]
    assert all(f"]({b['page']})" in llms for b in benchmarks)
    _, full = get(server, "/llms-full.txt")
    assert all(f"\n## {b['label']}\n" in full for b in benchmarks)
    keys = {b["key"] for b in benchmarks}
    scores = [s for s in get_json(server, "/api/v1/scores.json")["scores"] if s["benchmark"] in keys]
    assert full.count("\n| ") - full.count("\n| # |") == len(scores)
