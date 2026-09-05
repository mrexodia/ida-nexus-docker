import subprocess

import pytest

from run_site import cli, publish


def make_run(root, name="test-run"):
    run = root / "runs" / name
    (run / "workspace").mkdir(parents=True)
    (run / "workspace/report.md").write_text("# Analysis\n", encoding="utf-8")
    return run


def test_default_library_tracks_multiple_runs_and_rebuilds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = make_run(tmp_path, "run #1")
    second = make_run(tmp_path, "run-2")
    assert cli.main([str(first), "--title", "Report <one>"]) == 0
    assert cli.main([str(second)]) == 0
    index = tmp_path / "sites/index.html"
    text = index.read_text(encoding="utf-8")
    assert 'href="run%20%231/index.html"' in text
    assert 'href="run-2/index.html"' in text
    assert "Report &lt;one&gt;" in text
    assert not (first / "index.html").exists()
    assert cli.main([str(first)]) == 1
    assert index.read_text(encoding="utf-8") == text
    assert cli.main([str(first), "--overwrite", "--title", "Updated"]) == 0
    assert "Updated" in index.read_text(encoding="utf-8")
    assert 'href="run-2/index.html"' in index.read_text(encoding="utf-8")


def test_custom_library_and_standalone_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = make_run(tmp_path)
    assert cli.main([str(run), "--sites-dir", "converted"]) == 0
    assert (tmp_path / "converted/index.html").exists()
    assert (tmp_path / "converted/test-run/index.html").exists()
    assert cli.main([str(run), "--output", "standalone"]) == 0
    assert (tmp_path / "standalone/catalog.json").exists()
    assert not (tmp_path / "sites").exists()
    assert not (tmp_path / "index.html").exists()


def test_unrelated_library_index_is_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = make_run(tmp_path)
    (tmp_path / "sites").mkdir()
    index = tmp_path / "sites/index.html"
    index.write_text("unrelated site")
    assert cli.main([str(run), "--overwrite"]) == 1
    assert index.read_text() == "unrelated site"
    assert not (tmp_path / "sites/test-run").exists()


def test_publish_only_runs_after_successful_local_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = make_run(tmp_path)
    calls = []

    def fake_publish(output, run_id):
        assert (output / "index.html").is_file()
        assert (output.parent / "index.html").is_file()
        calls.append((output, run_id))
        return {"ok": True, "url": "https://example.pages.dev/p/test-run/"}

    monkeypatch.setattr(cli, "publish_site", fake_publish)
    assert cli.main([str(run)]) == 0
    assert calls == []
    assert cli.main([str(run), "--publish"]) == 1  # Rebuild needs --overwrite.
    assert calls == []
    assert cli.main([str(run), "--overwrite", "--publish"]) == 0
    assert calls == [(tmp_path / "sites/test-run", "test-run")]


def test_publish_failure_preserves_local_site(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    run = make_run(tmp_path)

    def unavailable(*args):
        raise RuntimeError("Pagecast is not connected")

    monkeypatch.setattr(cli, "publish_site", unavailable)
    assert cli.main([str(run), "--publish"]) == 1
    assert (tmp_path / "sites/test-run/catalog.json").exists()
    assert (tmp_path / "sites/index.html").exists()
    assert "Pagecast is not connected" in capsys.readouterr().err


def test_pagecast_command_publishes_only_one_run_with_stable_identity(
    tmp_path, monkeypatch
):
    output = tmp_path / "sites/run & one"
    calls = []
    monkeypatch.setattr(publish, "npx_command", lambda: ["node", "npx-cli.js"])

    def invoke(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            'progress\n{"ok":true,"url":"https://example.pages.dev/p/run/"}\n',
            "",
        )

    monkeypatch.setattr(publish.subprocess, "run", invoke)
    result = publish.publish_site(output, "run & one")
    assert result["url"] == "https://example.pages.dev/p/run/"
    assert calls[0][0] == [
        "node",
        "npx-cli.js",
        "--yes",
        "pagecast@0.7.0",
        "publish",
        str(output / "index.html"),
        "--context-id",
        "ida-run:run & one",
        "--item-key",
        "run & one",
        "--expires",
        "never",
        "--non-interactive",
        "--json",
    ]
    assert not calls[0][1].get("shell", False)


@pytest.mark.parametrize(
    "code,stdout,stderr",
    [
        (1, '{"ok":false,"error":"setup required"}', ""),
        (0, '{"ok":false,"error":"setup required"}', ""),
        (0, '{"ok":true}', ""),
        (1, "", "npm failed"),
        (0, "not JSON", ""),
    ],
)
def test_pagecast_errors_are_not_reported_as_success(
    tmp_path, monkeypatch, code, stdout, stderr
):
    monkeypatch.setattr(publish, "npx_command", lambda: ["npx"])
    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], code, stdout, stderr
        ),
    )
    with pytest.raises(RuntimeError, match="local site remains"):
        publish.publish_site(tmp_path, "run")


def test_pagecast_timeout_explains_uncertain_remote_status(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "npx_command", lambda: ["npx"])

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 600)

    monkeypatch.setattr(publish.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="remote publish may have completed"):
        publish.publish_site(tmp_path, "run")
