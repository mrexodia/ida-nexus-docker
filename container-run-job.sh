#!/usr/bin/env bash
# Run every mounted prompt as an independent Pi session, then package all IDA
# Nexus traces, linked Pi sessions, and worker logs into one portable ZIP.
set -uo pipefail

prompt_dir="${PROMPT_DIR:-/prompts}"
archive="${LOG_ARCHIVE_PATH:-/state/ida-nexus-logs.zip}"
run_id="${RUN_ID:-ida-analysis}"
reliable_execution="${RUNNER_RELIABLE_EXECUTION:-false}"
if [[ "$reliable_execution" != "true" && "$reliable_execution" != "false" ]]; then
    echo "error: RUNNER_RELIABLE_EXECUTION must be true or false" >&2
    exit 2
fi

pi_system_args=()
if [[ "$reliable_execution" == "true" ]]; then
    execution_contract=$'You are an execution agent that can call tools.\nNever write explanatory text in the same turn as a tool call. Either call a tool, or speak, but never both.\nNever emit tool-call JSON as ordinary text; use the native tool-call mechanism.\nNever use past tense about an action unless a tool result in this conversation shows it completed. A claim such as "I created X" is permitted only after a tool result confirms X exists.\nIf you intend to do something, do it; do not announce it. Continue calling tools until the requested work is complete or no defensible path remains.\nBefore the final answer, use tools to verify requested artifacts and state. In the final answer, clearly distinguish verified completed work from anything incomplete or blocked.'
    pi_system_args=(--append-system-prompt "$execution_contract")
fi

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
        "${pi_system_args[@]}" \
        --provider "$RUNNER_PROVIDER" \
        --model "$RUNNER_MODEL" \
        --thinking "${RUNNER_THINKING:-off}" \
        --no-context-files \
        --no-approve \
        --name "$stage_name" \
        -p "$(<"$prompt")"
    stage_status=$?

    if ((stage_status != 0)); then
        echo "stage $stage failed with status $stage_status; archiving partial logs" >&2
        status=$stage_status
        break
    fi

    result_path="$(printf '/workspace/%02d-result.md' "$stage")"
    extraction_args=()
    if [[ "$reliable_execution" == "true" ]]; then
        extraction_args+=(--validate-execution)
    fi
    python3 /opt/ida-runner/extract-final-response.py \
        "$PI_CODING_AGENT_SESSION_DIR" \
        --session-name "$stage_name" \
        --output "$result_path" \
        "${extraction_args[@]}"
    result_status=$?

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
