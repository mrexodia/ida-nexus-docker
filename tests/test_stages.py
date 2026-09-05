import json

from run_site.stages import build_stages, session_metadata


def setup_run(tmp_path, texts=("Unpack the sample.\n", "Write the report.")):
    run = tmp_path / "run"
    prompts = run / "prompts"
    prompts.mkdir(parents=True)
    paths = []
    for filename, text in zip(("001-unpack.txt", "002-report.txt"), texts):
        path = prompts / filename
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    manifest = {
        "run_id": "example",
        "prompts": [{"mounted_name": p.name} for p in paths],
    }
    return run, manifest, paths


def session(name, prompt=None, path="state/pi-sessions/a.jsonl"):
    return {
        "name": name,
        "names": [name],
        "prompt": prompt,
        "path": path,
        "page": path + ".html",
    }


def test_exact_stage_names_override_prompt_and_file_order(tmp_path):
    run, manifest, paths = setup_run(tmp_path)
    sessions = [
        session(
            "example-002-002-report.txt",
            "Unpack the sample.",
            "state/pi-sessions/a.jsonl",
        ),
        session("example-001-001-unpack.txt", path="state/pi-sessions/z.jsonl"),
    ]
    stages, unmatched = build_stages(run, manifest, paths[::-1], sessions, [])
    assert [stage["title"] for stage in stages] == ["Unpack", "Report"]
    assert stages[0]["sessions"][0]["path"].endswith("z.jsonl")
    assert stages[1]["sessions"][0]["path"].endswith("a.jsonl")
    assert stages[1]["sessions"][0]["matched_by"] == "session_name"
    assert unmatched == []


def test_exact_prompt_fallback_normalizes_shell_line_endings(tmp_path):
    run, manifest, paths = setup_run(
        tmp_path, ("First line\n\nSecond line\n", "Report")
    )
    stages, unmatched = build_stages(
        run,
        manifest,
        paths,
        [session("renamed", "First line\r\n\r\nSecond line\r")],
        [],
    )
    assert stages[0]["sessions"][0]["matched_by"] == "prompt_text"
    assert not stages[1]["sessions"]
    assert not unmatched


def test_duplicate_prompts_remain_unassigned(tmp_path):
    run, manifest, paths = setup_run(tmp_path, ("Same prompt", "Same prompt"))
    stages, unmatched = build_stages(
        run, manifest, paths, [session("renamed", "Same prompt")], []
    )
    assert len(unmatched) == 1
    assert all(not stage["sessions"] for stage in stages)


def test_missing_prompt_text_can_come_from_named_session(tmp_path):
    run, manifest, paths = setup_run(tmp_path)
    paths[0].unlink()
    stages, unmatched = build_stages(
        run,
        manifest,
        paths[1:],
        [session("example-001-001-unpack.txt", "Original text")],
        [],
    )
    assert stages[0]["prompt"] == "Original text"
    assert stages[0]["prompt_source"] == "session"
    assert stages[0]["prompt_page"] is None
    assert not unmatched


def test_retries_are_kept_together_and_results_use_stage_number(tmp_path):
    run, manifest, paths = setup_run(tmp_path)
    sessions = [
        session("example-001-001-unpack.txt", path=f"state/pi-sessions/{name}.jsonl")
        for name in ("b", "a")
    ]
    results = [
        {"path": path, "page": path + ".html"}
        for path in (
            "workspace/01-result.md",
            "workspace/01-result.runner-response.md",
            "workspace/02-result.md",
        )
    ]
    stages, unmatched = build_stages(run, manifest, paths, sessions, results)
    assert len(stages[0]["sessions"]) == 2
    assert len(stages[0]["results"]) == 2
    assert len(stages[1]["results"]) == 1
    assert not unmatched


def test_metadata_keeps_name_history_and_first_user_prompt(tmp_path):
    path = tmp_path / "session.jsonl"
    records = [
        {"type": "session", "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "session_info", "name": "runner-name"},
        {
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Original"}],
            },
        },
        {"type": "session_info", "name": "new-name"},
        {"type": "message", "message": {"role": "user", "content": "Follow-up"}},
    ]
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + '\n{"partial":', encoding="utf-8"
    )
    metadata = session_metadata(path)
    assert metadata["names"] == ["runner-name", "new-name"]
    assert metadata["name"] == "new-name"
    assert metadata["prompt"] == "Original"
    assert metadata["invalid_lines"] == 1


def test_missing_manifest_uses_prompts_without_guessing_sessions(tmp_path):
    run, _, paths = setup_run(tmp_path)
    stages, unmatched = build_stages(
        run, {}, paths, [session("unknown", "Something else")], []
    )
    assert len(stages) == 2
    assert len(unmatched) == 1
    assert not stages[0]["sessions"]
