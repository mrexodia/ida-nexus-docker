#!/usr/bin/env python3
"""Extract the last textual assistant response from a Pi JSONL session."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def session_name(path: Path) -> str | None:
    with path.open("r", encoding="utf-8") as jsonl:
        for line in jsonl:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("type") == "session_info":
                return record.get("name")
    return None


def select_session(source: Path, name: str | None) -> Path:
    if source.is_file():
        return source
    if not source.is_dir():
        raise ValueError(f"session source does not exist: {source}")
    if not name:
        raise ValueError("--session-name is required when the source is a directory")

    matches = [
        path for path in source.glob("*.jsonl") if session_name(path) == name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one session named {name!r} in {source}, found {len(matches)}"
        )
    return matches[0]


def extract_final_response(path: Path) -> str:
    final_text: str | None = None
    with path.open("r", encoding="utf-8") as jsonl:
        for line_number, line in enumerate(jsonl, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number} of {path}") from error

            message = record.get("message", {})
            if record.get("type") != "message" or message.get("role") != "assistant":
                continue

            content = message.get("content", [])
            if isinstance(content, str):
                parts = [content]
            else:
                parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
            text = "".join(parts).strip()
            if text:
                final_text = text

    if final_text is None:
        raise ValueError(f"no textual assistant response found in {path}")
    return final_text


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Pi JSONL file or session directory")
    parser.add_argument("--session-name", help="session name when source is a directory")
    parser.add_argument("-o", "--output", type=Path, help="Markdown output path")
    args = parser.parse_args()

    session = select_session(args.source, args.session_name)
    output = args.output or session.with_suffix(".md")
    write_atomic(output, extract_final_response(session))
    print(f"saved final response from {session} to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
