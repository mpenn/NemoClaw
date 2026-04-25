# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Hermes sandbox image — Hermes Agent + NemoClaw plugin inside OpenShell
#
# Layers PR-specific code (plugin, config, startup script) on top of the
# pre-built Hermes base image. Mirrors the OpenClaw Dockerfile structure.

ARG BASE_IMAGE=ghcr.io/nvidia/nemoclaw/hermes-sandbox-base:latest

FROM ${BASE_IMAGE}

# NeMo-Flow Phoenix OTLP endpoint — baked at image build time so start.sh
# reads it directly from the container ENV without relying on runtime injection.
# Empty string means no telemetry (standard behavior).
ARG PHOENIX_COLLECTOR_ENDPOINT=""
ENV PHOENIX_COLLECTOR_ENDPOINT=${PHOENIX_COLLECTOR_ENDPOINT}

# Harden: remove unnecessary build tools and network probes
RUN (apt-get remove --purge -y gcc gcc-12 g++ g++-12 cpp cpp-12 make \
        netcat-openbsd netcat-traditional ncat 2>/dev/null || true) \
    && apt-get autoremove --purge -y \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (gh) — not in the base image.
# GH_TOKEN is set below so gh authenticates via the OpenShell provider pipeline.
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Hermes v2026.4.13+ auto-detects HTTPS_PROXY and skips fallback-IP
# transport when a proxy is present. The sandbox proxy chain
# (decode-proxy → OpenShell L7 proxy) handles credential placeholder
# rewriting and hostname-based policy enforcement. No monkey patch needed.
ENV HERMES_TELEGRAM_DISABLE_FALLBACK_IPS=1 \
    GH_TOKEN=openshell:resolve:env:GITHUB_TOKEN

# Copy NemoClaw plugin for Hermes (Python-based)
COPY agents/hermes/plugin/ /opt/nemoclaw-hermes-plugin/

# Copy Outlook sidecar bridge and default cron jobs.
# Install under /usr/local/lib/ so Landlock's read_only /usr rule permits access.
COPY agents/hermes/bridges/ /usr/local/lib/nemoclaw-bridges/

# Copy config generation script and URL-decode proxy
COPY agents/hermes/generate-config.ts /opt/nemoclaw-generate-config.ts
COPY agents/hermes/patch-hermes-gateway.py /opt/nemoclaw-patch-hermes-gateway.py
COPY agents/hermes/decode-proxy.py /usr/local/bin/nemoclaw-decode-proxy
RUN chmod 755 /usr/local/bin/nemoclaw-decode-proxy
RUN python3 /opt/nemoclaw-patch-hermes-gateway.py

# Copy blueprint (shared infrastructure)
COPY nemoclaw-blueprint/ /opt/nemoclaw-blueprint/

# Ensure sandbox user can read all /opt/nemoclaw-* and bridge files.
# Source files may have restrictive permissions that Docker COPY preserves.
RUN chmod -R a+rX /opt/nemoclaw-hermes-plugin/ \
    && chmod -R a+rX /opt/nemoclaw-blueprint/ \
    && chmod a+r /opt/nemoclaw-generate-config.ts \
    && chmod a+r /opt/nemoclaw-patch-hermes-gateway.py \
    && chmod -R a+rX /usr/local/lib/nemoclaw-bridges/

# Copy startup script
COPY agents/hermes/start.sh /usr/local/bin/nemoclaw-start
RUN chmod 755 /usr/local/bin/nemoclaw-start

# Copy bundled agent memories and skills into the writable state directories.
# These are baked at build time so the agent knows its capabilities on first run
# without requiring a network fetch. The sandbox user owns these files.
COPY --chown=sandbox:sandbox agents/hermes/SOUL.md /sandbox/.hermes-data/SOUL.md
COPY --chown=sandbox:sandbox agents/hermes/skills/ /sandbox/.hermes-data/skills/

# Build args for config that varies per deployment.
ARG NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b
ARG NEMOCLAW_PROVIDER_KEY=custom
ARG NEMOCLAW_INFERENCE_BASE_URL=https://inference.local/v1
ARG CHAT_UI_URL=http://127.0.0.1:8642
ARG NEMOCLAW_MESSAGING_CHANNELS_B64=W10=
ARG NEMOCLAW_MESSAGING_ALLOWED_IDS_B64=e30=
ARG NEMOCLAW_BUILD_ID=default

# Promote build-args to env vars for the config generation script.
ENV NEMOCLAW_MODEL=${NEMOCLAW_MODEL} \
    NEMOCLAW_PROVIDER_KEY=${NEMOCLAW_PROVIDER_KEY} \
    NEMOCLAW_INFERENCE_BASE_URL=${NEMOCLAW_INFERENCE_BASE_URL} \
    CHAT_UI_URL=${CHAT_UI_URL} \
    NEMOCLAW_MESSAGING_CHANNELS_B64=${NEMOCLAW_MESSAGING_CHANNELS_B64} \
    NEMOCLAW_MESSAGING_ALLOWED_IDS_B64=${NEMOCLAW_MESSAGING_ALLOWED_IDS_B64}

WORKDIR /sandbox
USER sandbox

# Set up blueprint for local resolution
RUN mkdir -p /sandbox/.nemoclaw/blueprints/0.1.0 \
    && cp -r /opt/nemoclaw-blueprint/* /sandbox/.nemoclaw/blueprints/0.1.0/

# Generate Hermes config.yaml and .env from build args.
# config.yaml is immutable at runtime (Landlock read-only on /sandbox/.hermes).
# .env holds API key placeholders for OpenShell provider pipeline.
# SECURITY: Uses a separate script file instead of inline code to avoid
# code injection via build-arg interpolation (same concern as OpenClaw C-2).
RUN node --experimental-strip-types /opt/nemoclaw-generate-config.ts

# Install NemoClaw plugin into Hermes
RUN mkdir -p /sandbox/.hermes-data/plugins/nemoclaw \
    && cp -r /opt/nemoclaw-hermes-plugin/* /sandbox/.hermes-data/plugins/nemoclaw/

# Symlink SOUL.md into the immutable home so Hermes's ensure_hermes_home() finds it.
RUN ln -s /sandbox/.hermes-data/SOUL.md /sandbox/.hermes/SOUL.md

# Lock .hermes via DAC: chown to root so sandbox user cannot modify config.
# Same pattern as OpenClaw — Landlock provides defense-in-depth.
# hadolint ignore=DL3002
USER root
RUN chown root:root /sandbox/.hermes \
    && rm -rf /root/.cache/pip /sandbox/.cache \
    && find /sandbox/.hermes -mindepth 1 -maxdepth 1 -exec chown -h root:root {} + \
    && chmod 755 /sandbox/.hermes \
    && chmod 444 /sandbox/.hermes/config.yaml \
    && chmod 444 /sandbox/.hermes/.env

# Pin config hash at build time for integrity verification at startup.
RUN sha256sum /sandbox/.hermes/config.yaml /sandbox/.hermes/.env \
    > /sandbox/.hermes/.config-hash \
    && chmod 444 /sandbox/.hermes/.config-hash \
    && chown root:root /sandbox/.hermes/.config-hash

# start.sh handles privilege separation: runs as root initially to perform
# symlink validation and hardening, then drops to 'gateway' user via gosu
# for the agent process. See the root-path branch in start.sh.
ENTRYPOINT ["/usr/local/bin/nemoclaw-start"]
CMD ["/bin/bash"]
