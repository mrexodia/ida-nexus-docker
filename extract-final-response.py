#!/usr/bin/env python3
"""Extract the last textual assistant response from a Pi JSONL session."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
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


@dataclass
class SessionUsage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: Decimal = Decimal(0)

    @property
    def cache(self) -> int:
        return self.cache_read + self.cache_write

    def add(self, other: "SessionUsage") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.total_tokens += other.total_tokens
        self.cost += other.cost


def session_usage(path: Path) -> SessionUsage:
    total = SessionUsage()
    with path.open("r", encoding="utf-8") as jsonl:
        for line_number, line in enumerate(jsonl, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line, parse_float=Decimal)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number} of {path}") from error

            message = record.get("message", {})
            if record.get("type") != "message" or message.get("role") != "assistant":
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            cost = usage.get("cost")
            reported_cost = cost.get("total", 0) if isinstance(cost, dict) else 0
            entry = SessionUsage(
                input=int(usage.get("input", 0)),
                output=int(usage.get("output", 0)),
                cache_read=int(usage.get("cacheRead", 0)),
                cache_write=int(usage.get("cacheWrite", 0)),
                total_tokens=int(
                    usage.get(
                        "totalTokens",
                        sum(
                            int(usage.get(field, 0))
                            for field in ("input", "output", "cacheRead", "cacheWrite")
                        ),
                    )
                ),
                cost=Decimal(str(reported_cost)),
            )
            total.add(entry)
    return total


def format_cost(cost: Decimal) -> str:
    return f"${cost:.6f}"


def print_usage_report(paths: list[Path]) -> None:
    if not paths:
        raise ValueError("no Pi JSONL sessions found")

    sessions: list[tuple[str, SessionUsage]] = []
    total = SessionUsage()
    for path in paths:
        usage = session_usage(path)
        name = session_name(path) or path.stem
        sessions.append((name, usage))
        total.add(usage)

    print("\n=== Pi session usage ===")
    for name, usage in sessions:
        print(name)
        print(f"  cost:   {format_cost(usage.cost)}")
        print(
            "  tokens: "
            f"input={usage.input:,} output={usage.output:,} cache={usage.cache:,} "
            f"(read={usage.cache_read:,} write={usage.cache_write:,}) "
            f"total={usage.total_tokens:,}"
        )
    print("TOTAL")
    print(f"  cost:   {format_cost(total.cost)}")
    print(
        "  tokens: "
        f"input={total.input:,} output={total.output:,} cache={total.cache:,} "
        f"(read={total.cache_read:,} write={total.cache_write:,}) "
        f"total={total.total_tokens:,}"
    )


PSEUDO_TOOL_CALL = re.compile(
    r'^\s*\{\s*"(?:command|path|query|code)"\s*:', re.DOTALL
)


def validate_execution(path: Path) -> None:
    tool_calls = 0
    successful_tool_results = 0
    final_text: str | None = None

    with path.open("r", encoding="utf-8") as jsonl:
        for line_number, line in enumerate(jsonl, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number} of {path}") from error

            if record.get("type") != "message":
                continue
            message = record.get("message", {})
            role = message.get("role")
            if role == "assistant":
                content = message.get("content", [])
                if isinstance(content, str):
                    parts = [content]
                else:
                    parts = []
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "toolCall":
                            tool_calls += 1
                        elif item.get("type") == "text":
                            parts.append(item.get("text", ""))
                text = "".join(parts).strip()
                if text:
                    final_text = text
            elif role == "toolResult" and not message.get("isError", False):
                successful_tool_results += 1

    if tool_calls == 0:
        raise ValueError(f"session made no tool calls: {path}")
    if successful_tool_results == 0:
        raise ValueError(f"session had no successful tool results: {path}")
    if final_text is not None and PSEUDO_TOOL_CALL.match(final_text):
        raise ValueError(
            f"final response begins with an unexecuted pseudo-tool call: {path}"
        )


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
    parser.add_argument(
        "--usage-report",
        action="store_true",
        help="print per-session cost/token usage and aggregate totals",
    )
    parser.add_argument(
        "--validate-execution",
        action="store_true",
        help="require successful tool use and reject pseudo-tool-call text",
    )
    args = parser.parse_args()

    if args.usage_report:
        if args.output:
            parser.error("--output cannot be used with --usage-report")
        if args.source.is_file() or args.session_name:
            sessions = [select_session(args.source, args.session_name)]
        elif args.source.is_dir():
            sessions = sorted(args.source.glob("*.jsonl"))
        else:
            raise ValueError(f"session source does not exist: {args.source}")
        print_usage_report(sessions)
        return 0

    session = select_session(args.source, args.session_name)
    if args.validate_execution:
        validate_execution(session)
    output = args.output or session.with_suffix(".md")
    write_atomic(output, extract_final_response(session))
    print(f"saved final response from {session} to {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
