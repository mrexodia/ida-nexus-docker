#!/usr/bin/env python3
"""Launch one isolated, reproducible IDA Nexus analysis job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_IMAGE = "ida-nexus-runner:9.4"
DEFAULT_BASE_IMAGE = "ida:9.4"
DEFAULT_MODELS_FILE = Path(__file__).resolve().parent / "models.json"
SAMPLE_PLACEHOLDER = re.compile(r"\{SAMPLE([1-9][0-9]*)\}")
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    return (cleaned or "analysis")[:48]


def source_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label} is not a regular file: {value}")
    return path


def load_model_selection(path: Path, provider: str | None, model: str | None) -> tuple[str, str]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read models configuration {path}: {error}") from error

    providers = config.get("providers") if isinstance(config, dict) else None
    if not isinstance(providers, dict) or not providers:
        raise SystemExit(f"models configuration has no providers: {path}")

    selected_provider = provider or next(iter(providers))
    provider_config = providers.get(selected_provider)
    if not isinstance(provider_config, dict):
        raise SystemExit(f"provider {selected_provider!r} is not defined in {path}")

    configured_models = provider_config.get("models")
    model_ids = [
        item.get("id")
        for item in configured_models
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ] if isinstance(configured_models, list) else []
    selected_model = model or (model_ids[0] if model_ids else None)
    if not selected_model:
        raise SystemExit(
            f"provider {selected_provider!r} has no model to select; use --model"
        )
    if model_ids and selected_model not in model_ids:
        raise SystemExit(
            f"model {selected_model!r} is not defined for provider {selected_provider!r} in {path}"
        )
    return selected_provider, selected_model


def render_prompt(template: Path, sample_paths: list[str]) -> tuple[str, dict[str, str]]:
    try:
        text = template.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SystemExit(f"cannot read prompt as UTF-8 {template}: {error}") from error

    indexes = {int(match.group(1)) for match in SAMPLE_PLACEHOLDER.finditer(text)}
    missing = sorted(index for index in indexes if index > len(sample_paths))
    if missing:
        names = ", ".join(f"{{SAMPLE{index}}}" for index in missing)
        raise SystemExit(f"prompt {template} references unavailable sample(s): {names}")

    substitutions = {
        f"{{SAMPLE{index}}}": sample_paths[index - 1]
        for index in sorted(indexes)
    }
    return SAMPLE_PLACEHOLDER.sub(
        lambda match: sample_paths[int(match.group(1)) - 1], text
    ), substitutions


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_streaming(
    command: list[str], log_path: Path, *, env: dict[str, str] | None = None
) -> int:
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write("$ " + subprocess.list2cmdline(command) + "\n")
        log.flush()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
                log.flush()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            raise
        return process.wait()


def inspect_image(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def validate_archive(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"expected log archive was not created: {path}")
    with zipfile.ZipFile(path) as archive:
        try:
            toc = json.loads(archive.read("ida-nexus-logs.json"))
        except KeyError as error:
            raise RuntimeError("log archive has no ida-nexus TOC") from error
        if toc.get("format") != "ida-nexus-logs" or toc.get("schema") != 1:
            raise RuntimeError("unsupported ida-nexus log archive")
        files = toc.get("files") or []
        semantic = sum(item.get("kind") == "semantic_session" for item in files)
        agents = sum(item.get("kind") == "agent_session" for item in files)
        if semantic < 1:
            raise RuntimeError("log archive contains no semantic IDA sessions")
        if agents < 1:
            raise RuntimeError("log archive contains no linked Pi sessions")
        return {
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "semantic_sessions": semantic,
            "agent_sessions": agents,
            "members": len(archive.infolist()),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy samples into a fresh workspace and analyze them in an isolated IDA container."
    )
    parser.add_argument("--sample", action="append", required=True, help="sample file; repeatable")
    parser.add_argument("--prompt", action="append", required=True, help="prompt file in execution order; repeatable")
    parser.add_argument("--name", default="analysis", help="human-readable run name")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--image", default=os.environ.get("IDA_RUNNER_IMAGE", DEFAULT_IMAGE))
    parser.add_argument(
        "--base-image",
        default=os.environ.get("IDA_BASE_IMAGE", DEFAULT_BASE_IMAGE),
        help="Dockerfile base image (used with --build)",
    )
    parser.add_argument(
        "--models",
        type=Path,
        default=Path(os.environ.get("IDA_RUNNER_MODELS_FILE", DEFAULT_MODELS_FILE)),
        help="Pi models.json to mount (default: ./models.json beside analyze.py)",
    )
    parser.add_argument("--provider", default=os.environ.get("IDA_RUNNER_PROVIDER"))
    parser.add_argument("--model", default=os.environ.get("IDA_RUNNER_MODEL"))
    parser.add_argument(
        "--thinking",
        choices=THINKING_LEVELS,
        default=os.environ.get("IDA_RUNNER_THINKING", "off"),
        help="Pi thinking level (default: off)",
    )
    parser.add_argument("--build", action="store_true", help="build the image before running")
    args = parser.parse_args()

    samples = [source_file(value, "sample") for value in args.sample]
    prompts = [source_file(value, "prompt") for value in args.prompt]
    models_file = source_file(str(args.models), "models configuration")
    provider, model = load_model_selection(models_file, args.provider, args.model)

    sample_names: set[str] = set()
    for sample in samples:
        key = sample.name.casefold()
        if key in sample_names:
            raise SystemExit(f"duplicate sample basename: {sample.name}")
        sample_names.add(key)

    container_sample_paths = [f"/workspace/{sample.name}" for sample in samples]
    rendered_prompts = [
        (prompt, *render_prompt(prompt, container_sample_paths)) for prompt in prompts
    ]

    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{run_stamp}-{slug(args.name)}-{uuid.uuid4().hex[:6]}"
    run_root = args.runs_dir.expanduser().resolve() / run_id
    workspace = run_root / "workspace"
    prompt_dir = run_root / "prompts"
    state = run_root / "state"
    pi_sessions = state / "pi-sessions"
    nexus_state = state / "ida-nexus"
    for directory in (workspace, prompt_dir, pi_sessions, nexus_state):
        directory.mkdir(parents=True, exist_ok=False)

    copied_samples = []
    for sample in samples:
        destination = workspace / sample.name
        shutil.copy2(sample, destination)
        copied_samples.append(
            {"source": str(sample), "workspace_name": sample.name, "sha256": sha256(destination), "bytes": destination.stat().st_size}
        )

    copied_prompts = []
    for index, (prompt, rendered, substitutions) in enumerate(rendered_prompts, 1):
        destination = prompt_dir / f"{index:03d}-{slug(prompt.name)}"
        destination.write_text(rendered, encoding="utf-8")
        copied_prompts.append(
            {
                "source": str(prompt),
                "source_sha256": sha256(prompt),
                "mounted_name": destination.name,
                "sha256": sha256(destination),
                "bytes": destination.stat().st_size,
                "substitutions": substitutions,
            }
        )

    manifest_path = run_root / "manifest.json"
    console_log = state / "console.log"
    manifest: dict[str, Any] = {
        "schema": 1,
        "run_id": run_id,
        "name": args.name,
        "created_at": timestamp(),
        "status": "preparing",
        "image": args.image,
        "base_image": args.base_image,
        "provider": provider,
        "model": model,
        "thinking": args.thinking,
        "models": {
            "source": str(models_file),
            "sha256": sha256(models_file),
        },
        "samples": copied_samples,
        "prompts": copied_prompts,
        "paths": {
            "run": str(run_root),
            "workspace": str(workspace),
            "state": str(state),
            "log_archive": str(state / "ida-nexus-logs.zip"),
        },
    }
    write_manifest(manifest_path, manifest)

    try:
        subprocess.run(["docker", "version"], check=True, stdout=subprocess.DEVNULL)
        if args.build:
            manifest["status"] = "building"
            write_manifest(manifest_path, manifest)
            build_status = run_streaming(
                [
                    "docker", "build",
                    "--build-arg", f"BASE_IMAGE={args.base_image}",
                    "--tag", args.image,
                    str(Path(__file__).resolve().parent),
                ],
                console_log,
            )
            if build_status != 0:
                raise RuntimeError(f"Docker build failed with status {build_status}")
        manifest["image_id"] = inspect_image(args.image)

        container_name = f"ida-job-{slug(args.name).lower()}-{uuid.uuid4().hex[:8]}"
        command = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--label", f"ida-nexus.run-id={run_id}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=2048",
            "--add-host", "host.docker.internal:host-gateway",
            "--mount", f"type=bind,source={workspace},target=/workspace",
            "--mount", f"type=bind,source={prompt_dir},target=/prompts,readonly",
            "--mount", f"type=bind,source={state},target=/state",
            "--mount", f"type=bind,source={models_file},target=/config/models.json,readonly",
            "--env", f"RUN_ID={run_id}",
            "--env", f"RUNNER_PROVIDER={provider}",
            "--env", f"RUNNER_MODEL={model}",
            "--env", f"RUNNER_THINKING={args.thinking}",
            args.image,
            "/opt/ida-runner/container-run-job.sh",
        ]
        manifest["status"] = "running"
        manifest["container_name"] = container_name
        manifest["started_at"] = timestamp()
        write_manifest(manifest_path, manifest)
        started = time.monotonic()
        status = run_streaming(command, console_log)
        manifest["duration_seconds"] = round(time.monotonic() - started, 3)
        manifest["container_exit_code"] = status

        archive_path = state / "ida-nexus-logs.zip"
        archive_details = validate_archive(archive_path)
        manifest["log_archive"] = archive_details
        manifest["status"] = "completed" if status == 0 else "failed"
        manifest["completed_at"] = timestamp()
        write_manifest(manifest_path, manifest)
        print(f"\nrun:       {run_root}")
        print(f"workspace: {workspace}")
        print(f"logs:      {archive_path}")
        return status
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        manifest["completed_at"] = timestamp()
        write_manifest(manifest_path, manifest)
        return 130
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        manifest["completed_at"] = timestamp()
        write_manifest(manifest_path, manifest)
        print(f"error: {error}", file=sys.stderr)
        print(f"run state retained at {run_root}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
