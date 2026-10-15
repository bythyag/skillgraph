"""The dashboard's data API, static export and local server (no model calls)."""

import http.client
import json
import shutil
import threading

import pytest
from conftest import EXAMPLES

from skillgraph.dashboard import RunSets, export, serve


@pytest.fixture
def demo(tmp_path):
    copy = tmp_path / "examples"
    shutil.copytree(EXAMPLES, copy)
    return copy / "runs" / "demo"


def test_data_api(demo):
    sets = RunSets([demo, demo])
    assert list(sets.paths) == ["demo", "demo-2"]
    listed = sets.sets()
    assert listed[0]["runs"] == 40 and listed[0]["checked"] == 40
    data = sets.set("demo")
    assert data["error"] is None and len(data["data"]["skills"]) == 3 and len(data["runs"]) == 40
    row = next(r for r in data["runs"] if r["path"] == "release-notes/breaking-release/skill/r1")
    assert row["judge_mode"] == "full" and row["statuses"]["satisfied"] > 0 and row["reference"]
    run = sets.run("demo", row["path"])
    assert run["trace"]["task"] == "breaking-release" and run["digest"]["turns"] and "routes" not in run["digest"]
    assert run["claims"]["c1"]["text"] and run["reference"]
    diff = sets.diff("demo", "demo-2")
    assert all(c["change"] == "same" for s in diff["skills"] for c in s["claims"])


def test_new_fields_for_the_platform_ui(demo):
    sets = RunSets([demo])
    row = sets.sets()[0]
    assert row["tasks"] == 8 and row["has_reference"] and row["has_baseline"] and not row["imported"] and row["updated"]
    data = sets.set("demo")
    run = next(r for r in data["runs"] if r["path"] == "bugfix-tdd/slugify/baseline/r1")
    assert run["failed"] and run["prompt"].startswith("Bug report") and "wall_s" in run and "turns" in run
    sk = next(s for s in data["data"]["skills"] if s["skill"] == "bugfix-tdd")
    c8 = next(c for c in sk["claims"] if c["id"] == "c8")
    assert len(c8["results"]) == sk["totals"]["runs"]
    assert {"path", "status", "reason", "evidence", "method", "view"} <= set(c8["results"][0])
    rn = next(s for s in data["data"]["skills"] if s["skill"] == "release-notes")
    assert any(r["ref_statuses"] for r in rn["runs"])
    one = sets.run("demo", "csv-report/broken-data/skill/r1")
    assert len(one["turn_of_event"]) == len(one["trace"]["events"]) and one["turn_of_event"][0] == 0


def test_responses_are_cached_until_a_file_changes(demo):
    import os

    sets = RunSets([demo])
    first = sets.set("demo")
    assert sets.set("demo") is first  # served from the cache
    check = demo / "csv-report" / "broken-data" / "skill" / "r1" / "check.json"
    st = check.stat()
    os.utime(check, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert sets.set("demo") is not first  # a newer check.json invalidates it
    assert sets.diff("demo", "demo") is sets.diff("demo", "demo")


def test_run_paths_are_checked(demo):
    sets = RunSets([demo])
    with pytest.raises(ValueError):
        sets.run("demo", "../../../etc/passwd")
    with pytest.raises(ValueError):
        sets.run("demo", "a/b/c/../r1")
    with pytest.raises(ValueError):
        sets.run("demo", "a/../csv-report/r1")
    with pytest.raises(KeyError):
        sets.run("demo", "csv-report/nope/skill/r1")
    with pytest.raises(KeyError):
        sets.set("other")


def test_export_is_self_contained(demo, tmp_path):
    index = export(RunSets([demo]), tmp_path / "site")
    site = index.parent
    html = index.read_text()
    assert '<script src="data.js"></script>' in html and "/static/" not in html
    assert html.index('src="data.js"') < html.index('src="core.js"') < html.index('src="app.js"')
    for name in ("core.js", "pages.js", "trace.js", "app.js", "app.css", "tokens.css", "data.js", "report-demo.html"):
        assert (site / name).exists()
    data = (site / "data.js").read_text()
    assert data.startswith("window.SKILLGRAPH_DATA = ") and "</" not in data
    bundle = json.loads(data.removeprefix("window.SKILLGRAPH_DATA = ").rstrip(";\n"))
    assert "/api/sets" in bundle and "/api/set/demo" in bundle
    assert "/api/run/demo?path=csv-report/broken-data/skill/r1" in bundle


def test_server_serves_api_and_refuses_other_hosts(demo):
    ref, ready = {}, threading.Event()
    t = threading.Thread(target=serve, args=(RunSets([demo]),), daemon=True,
                         kwargs={"port": 0, "open_browser": False, "ready": ready, "server_ref": ref})
    t.start()
    assert ready.wait(10)
    port = ref["server"].server_address[1]

    def get(path, host=f"127.0.0.1:{port}"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path, headers={"Host": host})
        res = conn.getresponse()
        return res.status, dict(res.getheaders()), res.read()

    try:
        status, headers, body = get("/api/sets")
        assert status == 200 and json.loads(body)[0]["id"] == "demo"
        assert get("/api/sets", host="evil.example:80")[0] == 403
        status, headers, body = get("/")
        assert status == 200 and b"<title>skillgraph</title>" in body and "script-src 'self'" in headers["Content-Security-Policy"]
        assert get("/static/../../pyproject.toml")[0] == 404
        assert get("/static/runset.json")[0] == 404
        assert get("/api/run/demo?path=../../x")[0] == 400
        assert get("/api/set/nope")[0] == 404
        status, _, body = get("/api/run/demo?path=csv-report/broken-data/skill/r1")
        assert status == 200 and json.loads(body)["trace"]["skill"] == "csv-report"
        assert get("/report/demo")[0] == 200
        for name in ("core.js", "pages.js", "trace.js"):
            assert get(f"/static/{name}")[0] == 200
    finally:
        ref["server"].shutdown()
