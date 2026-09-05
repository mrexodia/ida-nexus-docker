"""Relate mounted prompts to native Pi sessions without assuming timestamp order."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from .metrics import message_usage, session_metrics, sum_usage, timestamp


def normalize_prompt(text: str) -> str:
    # Shell command substitution removes final newlines; mounted files can use CRLF.
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def session_metadata(path: Path) -> dict:
    usage = []
    starts, ends = [], []
    metadata = {
        "name": path.stem,
        "names": [],
        "prompt": None,
        "started_at": None,
        "ended_at": None,
        "invalid_lines": 0,
    }
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                record = json.loads(line, parse_float=Decimal)
            except json.JSONDecodeError:
                metadata["invalid_lines"] += 1
                continue
            if not isinstance(record, dict):
                continue
            message = record.get("message")
            if record.get("type") in ("session", "message"):
                recorded_at = timestamp(record.get("timestamp"))
                if recorded_at is None and isinstance(message, dict):
                    recorded_at = timestamp(message.get("timestamp"))
                if recorded_at is not None:
                    starts.append(recorded_at)
                    if record.get("type") == "message":
                        ends.append(recorded_at)
            if (
                record.get("type") == "message"
                and isinstance(message, dict)
                and message.get("role") == "assistant"
            ):
                usage.append(message_usage(message.get("usage")))
            if record.get("type") == "session_info" and isinstance(
                record.get("name"), str
            ):
                metadata["name"] = record["name"]
                metadata["names"].append(record["name"])
            if (
                record.get("type") == "message"
                and isinstance(message, dict)
                and message.get("role") == "user"
                and metadata["prompt"] is None
            ):
                content = message.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        block["text"]
                        for block in content
                        if isinstance(block, dict)
                        and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                    )
                if isinstance(content, str):
                    metadata["prompt"] = content
    metadata["started_at"] = min(starts).isoformat() if starts else None
    metadata["ended_at"] = max(ends).isoformat() if ends else None
    metadata["usage"] = sum_usage(usage)
    if metadata["invalid_lines"]:
        metadata["usage"]["complete"] = False
    return metadata


def build_stages(
    run: Path,
    manifest: dict,
    prompt_files: list[Path],
    sessions: list[dict],
    artifacts: list[dict],
) -> tuple[list[dict], list[dict]]:
    files = {p.relative_to(run).as_posix(): p for p in prompt_files}
    entries = manifest.get("prompts") or [
        {"mounted_name": p.relative_to(run / "prompts").as_posix()}
        for p in sorted(prompt_files)
    ]
    routes = {item["path"]: item["page"] for item in artifacts}
    stages = []
    for number, entry in enumerate(entries, 1):
        mounted = str(entry.get("mounted_name", ""))
        path = "prompts/" + mounted
        filename = mounted.rsplit("/", 1)[-1]
        title = (
            re.sub(r"^(?:\d+[-_])+", "", Path(filename).stem)
            .replace("-", " ")
            .replace("_", " ")
        )
        prompt = files[path].read_text(encoding="utf-8-sig") if path in files else None
        stages.append(
            {
                "number": number,
                "id": f"stage-{number:02d}",
                "title": title[:1].upper() + title[1:] or f"Stage {number}",
                "prompt_path": path,
                "prompt_page": routes.get(path),
                "prompt": prompt,
                "prompt_source": "file" if prompt is not None else None,
                "expected_name": f"{manifest.get('run_id', run.name)}-{number:03d}-{mounted}",
                "sessions": [],
                "results": [
                    item
                    for item in artifacts
                    if re.fullmatch(
                        rf"workspace/{number:02d}-result(?:\.[^/]+)?\.md", item["path"]
                    )
                ],
            }
        )
    unmatched = []
    for session in sorted(
        sessions, key=lambda item: (item.get("started_at") or "", item["path"])
    ):
        matches = [
            stage
            for stage in stages
            if stage["expected_name"] in session.get("names", [])
        ]
        method = "session_name"
        if not matches and session.get("prompt"):
            prompt = normalize_prompt(session["prompt"])
            matches = [
                stage
                for stage in stages
                if stage["prompt"] is not None
                and normalize_prompt(stage["prompt"]) == prompt
            ]
            method = "prompt_text"
        if len(matches) != 1:
            unmatched.append(session)
            continue
        stage = matches[0]
        session["stage"] = stage["number"]
        stage["sessions"].append(
            {
                key: session.get(key)
                for key in ("path", "page", "name", "started_at", "ended_at", "usage")
            }
        )
        stage["sessions"][-1]["matched_by"] = method
    for stage in stages:
        stage["metrics"] = session_metrics(stage["sessions"])
        if stage["prompt"] is None:
            prompts = {
                session["prompt"]
                for session in sessions
                if session.get("stage") == stage["number"] and session.get("prompt")
            }
            if len(prompts) == 1:
                stage["prompt"] = prompts.pop()
                stage["prompt_source"] = "session"
        del stage["expected_name"]
    return stages, unmatched
