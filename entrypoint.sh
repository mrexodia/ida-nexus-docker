#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
    "$IDA_NEXUS_STATE_DIR" \
    "$PI_CODING_AGENT_DIR" \
    "$PI_CODING_AGENT_SESSION_DIR"

# Import optional repository-local Pi resources into the disposable global
# config directory. Global placement makes them available in non-interactive
# mode without trusting project-local files in the hostile workspace.
repository_pi_config="${RUNNER_PI_CONFIG_DIR:-/config/pi}"
if [[ -d "$repository_pi_config" ]]; then
    for resource in extensions skills prompts; do
        source_dir="$repository_pi_config/$resource"
        if [[ -d "$source_dir" ]]; then
            destination_dir="$PI_CODING_AGENT_DIR/$resource"
            rm -rf "$destination_dir"
            mkdir -p "$destination_dir"
            cp -a "$source_dir/." "$destination_dir/"
        fi
    done

    if [[ -f "$repository_pi_config/auth.json" ]]; then
        install -m 600 "$repository_pi_config/auth.json" "$PI_CODING_AGENT_DIR/auth.json"
    fi

    # Preserve the IDA Nexus package installed in the image while applying all
    # repository settings over the image defaults.
    if [[ -f "$repository_pi_config/settings.json" ]]; then
        python3 - "$PI_CODING_AGENT_DIR/settings.json" "$repository_pi_config/settings.json" <<'PY'
import json
import os
import sys
from pathlib import Path

installed = Path(sys.argv[1])
incoming = Path(sys.argv[2])
base = json.loads(installed.read_text(encoding="utf-8")) if installed.is_file() else {}
override = json.loads(incoming.read_text(encoding="utf-8"))
if not isinstance(base, dict) or not isinstance(override, dict):
    raise SystemExit("Pi settings files must contain JSON objects")


def merge(left, right):
    result = dict(left)
    for key, value in right.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


merged = merge(base, override)
base_packages = base.get("packages") if isinstance(base.get("packages"), list) else []
override_packages = override.get("packages") if isinstance(override.get("packages"), list) else []
if base_packages:
    packages = []
    seen = set()
    for package in [*base_packages, *override_packages]:
        identity = json.dumps(package, sort_keys=True, separators=(",", ":"))
        if identity not in seen:
            seen.add(identity)
            packages.append(package)
    merged["packages"] = packages

temporary = installed.with_suffix(".json.tmp")
temporary.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, installed)
PY
    fi
fi

# The host supplies the complete Pi model catalog. Copy the read-only mount
# into Pi's config directory for this disposable container.
models_file="${RUNNER_MODELS_FILE:-/config/models.json}"
if [[ ! -f "$models_file" ]]; then
    echo "error: mount a Pi models.json at $models_file" >&2
    exit 2
fi
installed_models="$PI_CODING_AGENT_DIR/models.json"
install -m 600 "$models_file" "$installed_models"

# Select the first configured Pi provider/model unless the runner explicitly
# selected another one. models.json owns provider details; Nexus does not.
selection="$(python3 - "$installed_models" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
providers = config.get("providers")
if not isinstance(providers, dict) or not providers:
    raise SystemExit(f"models configuration has no providers: {path}")

provider = os.environ.get("RUNNER_PROVIDER") or next(iter(providers))
provider_config = providers.get(provider)
if not isinstance(provider_config, dict):
    raise SystemExit(f"provider {provider!r} is not defined in {path}")
models = provider_config.get("models")
model_ids = [
    item.get("id")
    for item in models
    if isinstance(item, dict) and isinstance(item.get("id"), str)
] if isinstance(models, list) else []
model = os.environ.get("RUNNER_MODEL") or (model_ids[0] if model_ids else None)
if not model:
    raise SystemExit(f"provider {provider!r} has no model to select")
if model_ids and model not in model_ids:
    raise SystemExit(f"model {model!r} is not defined for provider {provider!r}")
print(provider)
print(model)
PY
)"
mapfile -t selected <<<"$selection"
export RUNNER_PROVIDER="${selected[0]%$'\r'}"
export RUNNER_MODEL="${selected[1]%$'\r'}"
export RUNNER_THINKING="${RUNNER_THINKING:-off}"

exec "$@"
