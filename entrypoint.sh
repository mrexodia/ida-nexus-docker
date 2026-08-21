#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
    "$IDA_NEXUS_STATE_DIR" \
    "$PI_CODING_AGENT_DIR" \
    "$PI_CODING_AGENT_SESSION_DIR"

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
