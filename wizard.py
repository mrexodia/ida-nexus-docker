#!/usr/bin/env python3
"""Dependency-free interactive launcher for analyze.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
MODELS_FILE = PROJECT_ROOT / ".pi" / "models.json"
SAMPLES_DIR = PROJECT_ROOT / "samples"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def relative(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain a JSON object: {path}")
    return value


def list_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise RuntimeError(f"Directory does not exist: {directory}")
    return sorted(
        (
            path
            for path in directory.rglob("*")
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(directory).parts)
        ),
        key=lambda path: path.relative_to(directory).as_posix().casefold(),
    )


def parse_indexes(value: str, count: int) -> list[int]:
    selected: list[int] = []
    seen: set[int] = set()
    tokens = value.replace(",", " ").split()
    if len(tokens) == 1 and tokens[0].casefold() in {"all", "*"}:
        return list(range(count))

    for token in tokens:
        if "-" in token:
            parts = token.split("-", 1)
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError(f"invalid range: {token}")
            start, end = (int(part) for part in parts)
            step = 1 if end >= start else -1
            numbers = range(start, end + step, step)
        elif token.isdigit():
            numbers = [int(token)]
        else:
            raise ValueError(f"invalid selection: {token}")

        for number in numbers:
            if number < 1 or number > count:
                raise ValueError(f"selection out of range: {number}")
            index = number - 1
            if index not in seen:
                selected.append(index)
                seen.add(index)
    return selected


def choose_many(title: str, items: list[str], *, order_matters: bool = False) -> list[int]:
    if not items:
        raise RuntimeError(f"No choices available for {title.lower()}")
    print(f"\n{title}")
    for index, item in enumerate(items, 1):
        print(f"  {index:>2}. {item}")
    suffix = " in execution order" if order_matters else ""
    print(f"Enter numbers/ranges{suffix} (for example 1,3-5), or 'all'.")
    while True:
        raw = input("> ").strip()
        try:
            selected = parse_indexes(raw, len(items))
        except ValueError as error:
            print(f"Invalid selection: {error}")
            continue
        if selected:
            return selected
        print("Select at least one item.")


def choose_one(title: str, items: list[str], default: int = 0) -> int:
    print(f"\n{title}")
    for index, item in enumerate(items, 1):
        marker = " (default)" if index - 1 == default else ""
        print(f"  {index:>2}. {item}{marker}")
    while True:
        raw = input("> ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return int(raw) - 1
        print(f"Enter a number from 1 to {len(items)}.")


def ask_text(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def ask_yes_no(label: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{hint}]: ").strip().casefold()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter yes or no.")


def enumerate_models() -> list[dict[str, Any]]:
    catalog = load_object(MODELS_FILE, "models catalog")
    providers = catalog.get("providers")
    if not isinstance(providers, dict):
        raise RuntimeError(f"Models catalog has no providers object: {MODELS_FILE}")

    choices: list[dict[str, Any]] = []
    for provider, provider_config in providers.items():
        if not isinstance(provider, str) or not isinstance(provider_config, dict):
            continue
        models = provider_config.get("models")
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                continue
            choices.append({"provider": provider, **model})
    if not choices:
        raise RuntimeError(f"No models are defined in {MODELS_FILE}")
    return choices


def model_thinking_levels(model: dict[str, Any]) -> list[str]:
    if model.get("reasoning") is False:
        return ["off"]
    level_map = model.get("thinkingLevelMap")
    if not isinstance(level_map, dict):
        return list(THINKING_LEVELS)
    supported = ["off"]
    supported.extend(
        level for level in THINKING_LEVELS[1:] if level_map.get(level) is not None
    )
    return supported


def build_new_command() -> list[str]:
    samples = list_files(SAMPLES_DIR)
    prompts = list_files(PROMPTS_DIR)
    models = enumerate_models()

    sample_indexes = choose_many(
        "Select samples", [relative(path) for path in samples], order_matters=True
    )
    prompt_indexes = choose_many(
        "Select prompts", [relative(path) for path in prompts], order_matters=True
    )
    model_index = choose_one(
        "Select model",
        [
            f"{model['provider']} / {model['id']}"
            + (f" — {model['name']}" if model.get("name") else "")
            for model in models
        ],
    )
    model = models[model_index]
    levels = model_thinking_levels(model)
    thinking = levels[choose_one("Select thinking level", levels)]

    default_name = samples[sample_indexes[0]].stem[:48] or "analysis"
    name = ask_text("Run name", default_name)
    inject_prior = ask_yes_no("Inject prior-stage handoff", True)
    reliable = ask_yes_no("Enable reliable execution checks", False)
    build = ask_yes_no("Build the Docker image first", False)

    command = [sys.executable, str(PROJECT_ROOT / "analyze.py"), "--name", name]
    for index in sample_indexes:
        command.extend(["--sample", relative(samples[index])])
    for index in prompt_indexes:
        command.extend(["--prompt", relative(prompts[index])])
    command.extend(
        [
            "--models",
            relative(MODELS_FILE),
            "--provider",
            model["provider"],
            "--model",
            model["id"],
            "--thinking",
            thinking,
        ]
    )
    if not inject_prior:
        command.append("--no-inject-prior-stage")
    if reliable:
        command.append("--reliable-execution")
    if build:
        command.append("--build")
    return command


def main() -> int:
    print("IDA Nexus analysis wizard")
    command = build_new_command()

    print("\nCommand")
    print("  " + subprocess.list2cmdline(command))
    if not ask_yes_no("Start this run", True):
        print("Cancelled.")
        return 0
    return subprocess.call(command, cwd=PROJECT_ROOT)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        raise SystemExit(130)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
