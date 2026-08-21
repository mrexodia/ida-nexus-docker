# IDA Nexus Docker runner

A small, disposable analysis harness for IDA Pro 9.4, Pi, and
[IDA Nexus](https://github.com/HexRaysSA/ida-nexus). Each invocation copies
selected samples into a fresh workspace, runs ordered prompts in an isolated
container, and produces one portable ZIP containing the IDA Nexus and Pi audit
trail.

The image contains tools only. Samples, prompts, model settings, and credentials
are never baked into it.

## Configure models

Copy the example Pi model catalog and edit it for your provider:

```bash
cp models.example.json models.json
```

`models.json` is ignored by Git and Docker. Put the provider URL, API key, model
limits, and compatibility settings directly in that file. The launcher mounts
the selected file read-only and the entrypoint copies it to Pi's configuration
directory inside the disposable container.

By default, the first Pi provider and its first model are selected. Override
that selection with `--provider` and `--model`, or with
`IDA_RUNNER_PROVIDER` and `IDA_RUNNER_MODEL`. Use
`--models path/to/models.json` or `IDA_RUNNER_MODELS_FILE` to select another
catalog.

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
`workspace/01-result.md`, `workspace/02-result.md`, and so on.

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

Provider URLs and credentials come exclusively from the selected Pi model
catalog; they are not passed through environment variables or command-line
options. `--thinking` accepts `off`, `minimal`, `low`, `medium`, `high`, `xhigh`,
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

The manifest records the model-catalog hash and both source and rendered prompt
hashes, but not the model catalog contents. The launcher validates that
`ida-nexus-logs.zip` contains at least one semantic IDA session and its linked
Pi transcript. The ZIP has its own TOC, source-path map, sizes, and SHA-256
hashes. Its SHA-256 is also recorded in the run manifest.

If a prompt fails, the container stops subsequent stages but still attempts to
archive all partial logs.

## Isolation model

Only the following per-run inputs are exposed to the container:

- `workspace/` → `/workspace` (read/write)
- rendered `prompts/` → `/prompts` (read-only)
- `state/` → `/state` (read/write)
- the selected `models.json` → `/config/models.json` (read-only)

Original sample paths, this source tree, the user profile, unrelated
credentials, and the Docker socket are not mounted. Samples are copied rather
than bind-mounted. The container drops Linux capabilities, enables
`no-new-privileges`, and uses normal Docker bridge networking. Pi ignores
workspace context files and project-local extensions for non-interactive jobs.

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
