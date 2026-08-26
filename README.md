# IDA Nexus Docker runner

A small, disposable analysis harness for IDA Pro 9.4, Pi, and
[IDA Nexus](https://github.com/HexRaysSA/ida-nexus). Each invocation copies
selected samples into a fresh workspace, runs ordered prompts in an isolated
container, and produces one portable ZIP containing the IDA Nexus and Pi audit
trail.

The image contains tools only. Samples, prompts, model settings, and credentials
are never baked into it.

## Configure Pi

Repository-local Pi configuration lives under `.pi/`:

```text
.pi/
  models.json
  auth.json
  settings.json
  extensions/
  skills/
  prompts/
```

Create the model catalog from the example:

```bash
cp models.example.json .pi/models.json
```

`.pi/models.json` and `.pi/auth.json` are ignored by Git, and the entire `.pi`
directory is excluded from the Docker build context. Provider credentials may
remain as `apiKey` values in `models.json`, but the recommended layout stores
them in `.pi/auth.json`:

```json
{
  "provider-name": { "type": "api_key", "key": "..." }
}
```

At runtime the launcher mounts `.pi` read-only. The entrypoint copies
`auth.json`, merged `settings.json`, and the `extensions/`, `skills/`, and
`prompts/` trees into Pi's disposable global configuration directory. Global
placement makes these resources available to print-mode sessions even though
the hostile workspace remains untrusted. The image's IDA Nexus package setting
is preserved when repository settings are merged.

Only files that exist are imported. Use `--pi-config-dir path` or
`IDA_RUNNER_PI_CONFIG_DIR` to select another directory. Pi extensions execute
arbitrary code inside the container, and skills can direct model behavior, so
review both before unattended runs. `.pi/prompts/` contains Pi slash-command
prompt templates; the top-level `prompts/` directory still contains ordered
analysis stages supplied with `--prompt`.

By default, the first Pi provider and its first model are selected. Override
that selection with `--provider` and `--model`, or with
`IDA_RUNNER_PROVIDER` and `IDA_RUNNER_MODEL`. Model selection prefers
`.pi/models.json`, then the legacy root `models.json`. Use
`--models path/to/models.json` or `IDA_RUNNER_MODELS_FILE` to select another
catalog. The example applies `temperature=1.0`, `top_p=0.95`, and `top_k=64`
through Pi's model-level `samplingParams`; OpenAI-compatible APIs merge supported
values verbatim into every request.

## Build

```bash
docker compose build
```

The default IDA base image is only a convenience and can be replaced without
editing the Dockerfile:

```bash
IDA_BASE_IMAGE=registry.example.com/ida:9.4 docker compose build
```

The equivalent direct build option is:

```bash
docker build --build-arg BASE_IMAGE=registry.example.com/ida:9.4 \
  -t ida-nexus-runner:9.4 .
```

The base image must provide a working IDA Pro 9.4+ installation with idalib.
Set `IDA_RUNNER_IMAGE` to change the Compose image tag. `analyze.py` also accepts
`--image`, and passes `--base-image` as `BASE_IMAGE` when used with `--build`.

The HCLI documentation has an example [IDA Pro Docker Container](https://github.com/HexRaysSA/ida-hcli/tree/main/docs/advanced/docker) guide you can use as a base. It works with IDA Home as well as IDA Pro.

## Run

```bash
python analyze.py \
  --name darkside \
  --sample e51e4c372edf2bbe476a4b7630225c1875c5ccea2ed55b418bd793c54ce9a84d.exe \
  --prompt prompts/01-unpack.txt \
  --prompt prompts/02-recover-config.txt \
  --prompt prompts/03-markup.txt
```

`--sample` and `--prompt` are repeatable. Prompts run in the order supplied and
each receives a separate Pi session. They communicate through the persistent
workspace and IDB rather than an ever-growing model transcript. After each
prompt, the harness extracts its final textual assistant response to
`workspace/01-result.md`, `workspace/02-result.md`, and so on. At the end of the
console output it prints each session's total reported cost and input, output,
and cache token usage, followed by aggregate cost and token totals for the run.

By default, each stage after the first is told to read only the immediately
preceding `result.md` as untrusted prior-stage notes and to verify its important
claims against the IDB and generated artifacts. The result contents are not
inserted into the prompt, and the full result chain is not injected. Disable
this handoff with `--no-inject-prior-stage`; enable it explicitly with
`--inject-prior-stage`.

Reliable execution is opt-in with `--reliable-execution`. Pi then receives an
appended system contract that separates tool-call turns from explanatory turns,
forbids printing tool-call JSON as text, prohibits unsupported completion
claims, and requires artifact/state verification before the final answer. The
stage is rejected if its JSONL contains no successful tool use or its final
response starts with an unexecuted pseudo-tool call, preventing that response
from being passed to the next stage. This catches obvious false-success modes
but cannot prove that a model's analysis is correct.

Prompt templates may use `{SAMPLE1}`, `{SAMPLE2}`, and so on. The placeholders
are one-indexed in `--sample` order and are replaced in the mounted prompt copies
with paths such as `/workspace/sample.exe`. A prompt that references a missing
sample is rejected before the run starts.

Useful overrides:

```bash
python analyze.py ... \
  --models ~/.config/pi/models.json \
  --provider local-dspark \
  --model deepseek-v4-flash-0731 \
  --thinking high
```

Provider URLs come from the selected Pi model catalog. Credentials resolve
through Pi's normal order, including `.pi/auth.json` and model-catalog keys;
they are not passed through runner command-line options. `--thinking` accepts
`off`, `minimal`, `low`, `medium`, `high`, `xhigh`,
or `max` and defaults to `off`; `IDA_RUNNER_THINKING` sets a different runner
default. The selected model must declare `"reasoning": true`, and its optional
`thinkingLevelMap` in `models.json` controls which levels Pi supports and how
they map to provider values.

## Per-run output

```text
runs/<timestamp>-<name>-<id>/
  manifest.json
  workspace/                  copied samples + analysis outputs/IDBs
    01-result.md              final response from the first prompt
    02-result.md              final response from the second prompt
  prompts/                    rendered copies of submitted prompt templates
  state/
    console.log
    ida-nexus-logs.zip
    pi-sessions/              native Pi JSONL sessions
    ida-nexus/
      sessions/               native semantic IDA traces
      logs/                   IDA worker stdout/stderr
      instances/
      spawn/
```

The manifest records hashes and sizes for imported Pi config/resources, the
model catalog, and both source and rendered prompts, but not configuration file
contents. For successful runs, the launcher
validates that `ida-nexus-logs.zip` contains at least one semantic IDA session
and its linked Pi transcript. The ZIP has its own TOC, source-path map, sizes,
and SHA-256 hashes. Its SHA-256 is also recorded in the run manifest.

If Pi exits unsuccessfully or reliable-execution validation rejects a session,
the container skips final-response extraction, stops subsequent stages, and
still attempts to archive all partial logs. A partial archive may contain no
linked Pi transcript; the launcher records its available contents without
replacing the original container failure with an archive-validation error.

## Isolation model

Only the following per-run inputs are exposed to the container:

- `workspace/` → `/workspace` (read/write)
- rendered `prompts/` → `/prompts` (read-only)
- `state/` → `/state` (read/write)
- the selected `models.json` → `/config/models.json` (read-only)
- `.pi/` (when present) → `/config/pi` (read-only)

Original sample paths, the rest of this source tree, the user profile, unrelated
credentials, and the Docker socket are not mounted. Samples are copied rather
than bind-mounted. The container drops Linux capabilities, enables
`no-new-privileges`, and uses normal Docker bridge networking. Pi ignores
workspace context files and project-local extensions for non-interactive jobs;
explicit repository `.pi` resources are copied into the container-global Pi
configuration instead.

Network access is still required for the configured model endpoint. Treat all
samples as hostile and use static-analysis prompts; container isolation is not
a substitute for a dedicated malware-analysis host.

## Inspect a log archive

The portable archive can be opened by IDA Nexus tooling or inspected as a
normal ZIP. It contains:

- `sessions/` — semantic IDA tool calls/results
- `agent-sessions/` — linked Pi transcripts
- `logs/` — worker operational logs
- `ida-nexus-logs.json` — archive TOC

Raw state remains alongside it under `state/` for troubleshooting.

## Included test material

The original sample and the three experiment prompts are intentionally retained
for local testing. `prompts/00-smoke-test.txt` provides a much cheaper end-to-end
check that opens `{SAMPLE1}` through IDA Nexus, queries it with the Domain API,
saves and closes the database, and exercises portable log export.
