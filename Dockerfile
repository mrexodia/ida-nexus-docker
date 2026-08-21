ARG BASE_IMAGE=ida:9.4
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_VERSION=22

ENV FNM_DIR=/root/.local/share/fnm \
    PATH=/root/.local/share/fnm/aliases/default/bin:/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UV_LINK_MODE=copy \
    PI_CODING_AGENT_DIR=/root/.pi/agent \
    PI_CODING_AGENT_SESSION_DIR=/state/pi-sessions \
    IDA_NEXUS_STATE_DIR=/state/ida-nexus \
    PI_SKIP_VERSION_CHECK=1 \
    PI_TELEMETRY=0

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git tini unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Node through fnm, Pi, and uv.
RUN curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell \
    && "$FNM_DIR/fnm" install "$NODE_VERSION" \
    && "$FNM_DIR/fnm" default "$NODE_VERSION" \
    && ln -s "$FNM_DIR/fnm" /usr/local/bin/fnm \
    && npm install -g --ignore-scripts @earendil-works/pi-coding-agent \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && node --version \
    && pi --version \
    && uv --version

# Install IDA Nexus from the published latest branch. Keep its uv project warm
# and expose its CLI globally so jobs can produce a portable log ZIP.
RUN pi install git:github.com/HexRaysSA/ida-nexus@latest \
    && extension_dir="$(find /root/.pi/agent/git -type f -name ida-nexus.ts -printf '%h\n' -quit)" \
    && test -n "$extension_dir" \
    && uv sync --project "$extension_dir" --no-dev \
    && uv run --with=ida-hcli --project "$extension_dir" ida-nexus --help >/dev/null \
    && ln -s "$extension_dir/.venv/bin/ida-nexus" /usr/local/bin/ida-nexus

COPY entrypoint.sh container-run-job.sh extract-final-response.py /opt/ida-runner/
RUN chmod +x \
    /opt/ida-runner/entrypoint.sh \
    /opt/ida-runner/container-run-job.sh \
    /opt/ida-runner/extract-final-response.py

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/ida-runner/entrypoint.sh"]
CMD ["bash"]
