#!/usr/bin/env bash
# Run every mounted prompt as an independent Pi session, then package all IDA
# Nexus traces, linked Pi sessions, and worker logs into one portable ZIP.
set -uo pipefail

prompt_dir="${PROMPT_DIR:-/prompts}"
archive="${LOG_ARCHIVE_PATH:-/state/ida-nexus-logs.zip}"
run_id="${RUN_ID:-ida-analysis}"

mapfile -d '' prompts < <(find "$prompt_dir" -maxdepth 1 -type f -print0 | sort -z)
if ((${#prompts[@]} == 0)); then
    echo "error: no prompt files found under $prompt_dir" >&2
    exit 2
fi

status=0
stage=0
for prompt in "${prompts[@]}"; do
    ((stage += 1))
    stage_name="$(printf '%s-%03d-%s' "$run_id" "$stage" "$(basename "$prompt")")"
    echo "=== stage $stage/${#prompts[@]}: $(basename "$prompt") ==="
    pi \
        --provider "$RUNNER_PROVIDER" \
        --model "$RUNNER_MODEL" \
        --thinking "${RUNNER_THINKING:-off}" \
        --no-context-files \
        --no-approve \
        --name "$stage_name" \
        -p "$(<"$prompt")"
    stage_status=$?

    result_path="$(printf '/workspace/%02d-result.md' "$stage")"
    python3 /opt/ida-runner/extract-final-response.py \
        "$PI_CODING_AGENT_SESSION_DIR" \
        --session-name "$stage_name" \
        --output "$result_path"
    result_status=$?

    if ((stage_status != 0)); then
        echo "stage $stage failed with status $stage_status; archiving partial logs" >&2
        status=$stage_status
        break
    fi
    if ((result_status != 0)); then
        echo "failed to extract the stage $stage final response (status $result_status)" >&2
        status=$result_status
        break
    fi
done

mkdir -p "$(dirname "$archive")"
ida-nexus logs --output "$archive" --force
archive_status=$?
if ((archive_status != 0)); then
    echo "failed to create IDA Nexus log archive (status $archive_status)" >&2
    if ((status == 0)); then
        status=$archive_status
    fi
fi

exit "$status"
