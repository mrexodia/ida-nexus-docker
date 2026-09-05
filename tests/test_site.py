import json
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from run_site.site import build_site, db_key, digest, native_pi_html, write_json


@pytest.fixture
def fixture(tmp_path):
    run = tmp_path / "run"
    workspace = run / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "nested").mkdir()
    (workspace / "sample").write_bytes(b"MZ\0SAMPLE")
    (workspace / "renamed.i64").write_bytes(b"test snapshot input")
    (workspace / "unmatched.exe").write_bytes(b"MZ\0OTHER")
    (workspace / "nested/report #1.md").write_text(
        "# Result\n\n[Sample](/workspace/sample)\n\n[Code](../analysis.py)\n\n"
        "`/workspace/renamed.i64`\n\n<script>alert(1)</script>\n\n"
        '```python\nprint("safe")\n```\n\n| Key | Value |\n| --- | --- |\n| a | b |\n',
        encoding="utf-8",
    )
    (workspace / "analysis.py").write_text('print("<script>")\n', encoding="utf-8")
    (workspace / "data.json").write_text(
        '{"x":"<img onerror=alert(1)>"}', encoding="utf-8"
    )
    sessions = run / "state/pi-sessions"
    sessions.mkdir(parents=True)
    (sessions / "session.jsonl").write_text('{"type":"session"}\n', encoding="utf-8")
    (sessions / "session.html").write_text(
        "<!doctype html><html><body>Native Pi export</body></html>", encoding="utf-8"
    )
    cache = tmp_path / "snapshots"
    snapshot = cache / db_key("workspace/renamed.i64")
    write_json(
        snapshot / "index.json",
        {
            "schema": 1,
            "source_sha256": digest(workspace / "renamed.i64"),
            "input_file": "/workspace/old.exe",
            "input_sha256": digest(workspace / "sample"),
            "functions": [],
            "types": [],
            "chunks": [],
            "segments": [],
            "names": [],
            "warnings": [],
            "head_count": 0,
        },
    )
    return SimpleNamespace(
        run=run,
        output=tmp_path / "site",
        ida_data=cache,
        skip_ida=False,
        title="Test <run>",
        ida_python="does-not-exist",
        chunk_size=2,
        max_functions=None,
        max_heads=None,
        ida_timeout=30,
        pi="does-not-exist",
    )


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src", "data-index"):
                self.links.append(value)


def test_complete_site_reuses_native_logs_and_resolves_links(fixture):
    before = digest(fixture.run / "workspace/renamed.i64")
    build_site(fixture)
    assert digest(fixture.run / "workspace/renamed.i64") == before
    assert (fixture.output / ".nojekyll").exists()
    report = (
        fixture.output / "artifacts/workspace/nested/report #1.md.html"
    ).read_text(encoding="utf-8")
    assert 'href="../sample.html"' in report
    assert 'href="../analysis.py.html"' in report
    assert 'href="../renamed.i64.html"' in report
    assert "<script>alert" not in report
    assert "&lt;script&gt;" in report
    assert "<table>" in report
    assert "<span class=" in report
    assert "<pre><code><div" not in report
    sample = (fixture.output / "artifacts/workspace/sample.html").read_text(
        encoding="utf-8"
    )
    assert 'id="redirect" href="renamed.i64.html"' in sample
    assert not any(p.suffix in (".exe", ".i64") for p in fixture.output.rglob("*"))
    native = next((fixture.output / "logs").glob("*.native.html")).read_text(
        encoding="utf-8"
    )
    assert "Native Pi export" in native
    assert "artifactRoutes" in native
    # Validate physical asset/page links under arbitrary hosting prefixes.
    for page in fixture.output.rglob("*.html"):
        parser = Links()
        parser.feed(page.read_text(encoding="utf-8"))
        for href in parser.links:
            parts = urlsplit(href)
            if not parts.scheme and not parts.netloc and parts.path:
                assert not parts.path.startswith("/"), (page, href)
                assert (page.parent / unquote(parts.path)).is_file(), (page, href)


def test_snapshot_mismatch_does_not_publish_partial_output(fixture):
    (fixture.run / "workspace/renamed.i64").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        build_site(fixture)
    assert not fixture.output.exists()


def test_refuse_overwrite(fixture):
    fixture.output.mkdir()
    marker = fixture.output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="new or empty"):
        build_site(fixture)
    assert marker.read_text() == "keep"


def test_overwrite_can_reuse_own_snapshot_and_preserves_previous_on_failure(fixture):
    build_site(fixture)
    fixture.overwrite = True
    fixture.ida_data = fixture.output / "data"
    fixture.title = "Rebuilt"
    build_site(fixture)
    index = fixture.output / "index.html"
    before = index.read_bytes()
    assert b"Rebuilt" in before
    (fixture.run / "workspace/renamed.i64").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        build_site(fixture)
    assert index.read_bytes() == before
    assert (fixture.output / "data").is_dir()


def test_overwrite_refuses_unowned_or_other_runs_output(fixture, tmp_path):
    fixture.overwrite = True
    fixture.output.mkdir()
    marker = fixture.output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="requires a site generated"):
        build_site(fixture)
    assert marker.read_text() == "keep"
    fixture.output = tmp_path / "generated"
    build_site(fixture)
    fixture.run = tmp_path / "different-run"
    (fixture.run / "workspace").mkdir(parents=True)
    with pytest.raises(ValueError, match="different run"):
        build_site(fixture)
    assert (fixture.output / "index.html").exists()


def test_refuse_output_among_inputs(fixture):
    fixture.output = fixture.run / "workspace/site"
    with pytest.raises(ValueError, match="separate"):
        build_site(fixture)


def test_missing_pi_html_requires_native_export(fixture):
    (fixture.run / "state/pi-sessions/session.html").unlink()
    with pytest.raises(RuntimeError, match="run pi --export"):
        build_site(fixture)
    assert not fixture.output.exists()


def test_pi_export_invocation(fixture, monkeypatch, tmp_path):
    source = fixture.run / "state/pi-sessions/session.jsonl"
    source.with_suffix(".html").unlink()
    destination = tmp_path / "native.html"
    monkeypatch.setattr("run_site.site.shutil.which", lambda name: "/tools/pi")
    calls = []

    def invoke(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_text("native HTML")

    monkeypatch.setattr("run_site.site.subprocess.run", invoke)
    native_pi_html(source, destination, fixture)
    assert calls == [["/tools/pi", "--export", str(source), str(destination)]]
    assert destination.read_text() == "native HTML"


def test_multiple_matching_databases_offer_selection(fixture):
    other = fixture.run / "workspace/second.i64"
    other.write_bytes(b"second database")
    cached = fixture.ida_data / db_key("workspace/renamed.i64") / "index.json"
    metadata = json.loads(cached.read_text())
    metadata["source_sha256"] = digest(other)
    write_json(
        fixture.ida_data / db_key("workspace/second.i64") / "index.json", metadata
    )
    build_site(fixture)
    sample = (fixture.output / "artifacts/workspace/sample.html").read_text(
        encoding="utf-8"
    )
    assert "Choose a database" in sample
    assert 'id="redirect"' not in sample
    assert "second.i64.html" in sample


def test_skip_ida_is_explicit_in_report(fixture):
    fixture.skip_ida = True
    build_site(fixture)
    catalog = json.loads((fixture.output / "catalog.json").read_text())
    assert any("explicitly skipped" in warning for warning in catalog["warnings"])


def test_overview_stages_tree_and_nexus_omission(fixture):
    prompts = fixture.run / "prompts"
    prompts.mkdir()
    prompt = '<script>alert("prompt")</script>\nAnalyze /workspace/sample.'
    (prompts / "001-analyze.txt").write_text(prompt, encoding="utf-8")
    write_json(
        fixture.run / "manifest.json",
        {"run_id": "test-run", "prompts": [{"mounted_name": "001-analyze.txt"}]},
    )
    write_json(
        fixture.run / "state/pi-sessions/session.jsonl",
        {"type": "session_info", "name": "test-run-001-001-analyze.txt"},
    )
    write_json(
        fixture.run / "state/ida-nexus/sessions/private.jsonl", {"tool": "internal"}
    )
    (fixture.run / "workspace/nested/page").write_text("ordinary filename")
    (fixture.run / "workspace/01-result.md").write_text("# Result")
    build_site(fixture)
    catalog = json.loads((fixture.output / "catalog.json").read_text())
    stage = catalog["stages"][0]
    assert stage["prompt"] == prompt
    assert stage["sessions"][0]["matched_by"] == "session_name"
    assert stage["results"][0]["path"] == "workspace/01-result.md"
    assert not any("ida-nexus" in log["path"] for log in catalog["logs"])
    assert not (fixture.output / "artifacts/state/ida-nexus").exists()
    overview = (fixture.output / "index.html").read_text(encoding="utf-8")
    assert 'id="stage-01"' in overview
    assert 'class="file-tree"' in overview
    assert 'class="artifact-card"' not in overview
    assert "&lt;script&gt;" in overview
    assert "<script>alert" not in overview
    assert "No matched session" not in overview
    wrapper = (fixture.output / stage["sessions"][0]["page"]).read_text(
        encoding="utf-8"
    )
    assert 'href="../index.html#stage-01"' in wrapper
