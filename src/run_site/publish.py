"""Optional Pagecast CLI adapter. It never configures or replaces a Pages project."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Keep the wrapper aligned with the CLI contract tested by this project.
PAGECAST_PACKAGE = "pagecast@0.7.0"


def npx_command() -> list[str]:
    executable = shutil.which("npx")
    if not executable:
        raise RuntimeError(
            "Publishing requires Node.js 20.19+ and npx. Install Node.js, then run npx pagecast pages setup."
        )
    if sys.platform == "win32":
        # Invoke npm's JS entry directly, avoiding cmd.exe interpretation of path characters.
        node = shutil.which("node")
        script = Path(executable).parent / "node_modules/npm/bin/npx-cli.js"
        if not node or not script.is_file():
            raise RuntimeError(
                "Cannot locate Node/npm's npx-cli.js; use a standard Node.js installation or publish with npx pagecast manually."
            )
        return [node, str(script)]
    return [executable]


def publish_site(output: Path, run_id: str) -> dict:
    command = npx_command() + [
        "--yes",
        PAGECAST_PACKAGE,
        "publish",
        str(output / "index.html"),
        "--context-id",
        f"ida-run:{run_id}",
        "--item-key",
        run_id,
        "--expires",
        "never",
        "--non-interactive",
        "--json",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Pagecast did not finish within 10 minutes. The local site remains at {output}. Check Pagecast before retrying; the remote publish may have completed."
        ) from error
    payload = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "ok" in candidate:
            payload = candidate
            break
    if (
        result.returncode
        or not payload
        or payload.get("ok") is not True
        or not isinstance(payload.get("url"), str)
    ):
        detail = (
            json.dumps(payload, ensure_ascii=False)
            if payload
            else result.stderr or result.stdout
        ).strip()
        raise RuntimeError(
            f"Pagecast publish failed; the local site remains at {output}.\n{detail[-4000:]}\nComplete setup with: npx pagecast pages setup --project YOUR-PROJECT"
        )
    return payload
