# NemoClaw Hermes Setup Guide (NVIDIA Internal)

This guide walks through standing up a NemoClaw Hermes sandbox with Slack integration
from scratch. It covers building from source, creating the Slack app, configuring
credentials, and running the first onboard.

---

## Prerequisites

- Linux host (Ubuntu 22.04+ recommended) with Docker installed and running
- Access to [NVIDIA API Catalog](https://build.nvidia.com) for an inference API key
- A Slack workspace where you have permission to create apps
- Node.js 22+ and npm
- Python 3.11+ and `uv`
- `git`

---

## 1. Clone and Build from Source

```bash
git clone https://github.com/NVIDIA/NemoClaw.git
cd NemoClaw

# Install all dependencies and build the plugin
npm install
cd nemoclaw && npm install && npm run build && cd ..
cd nemoclaw-blueprint && uv sync && cd ..
```

Verify the CLI works:

```bash
node bin/nemoclaw.js --version
```

---

## 2. Create the Slack App

NemoClaw communicates with Slack using a bot that you register through the Slack API
dashboard. The repo includes a pre-configured app manifest that sets the correct scopes
and event subscriptions.

### 2a. Create the app from the manifest

1. Copy `slack_app_manifest.json` to a local file and open it in a text editor.
2. Replace the three placeholder values with your own identifier:

   | Field | Placeholder | Example replacement |
   |-------|-------------|---------------------|
   | `display_information.name` | `MyUser NemoClaw Staging` | `Alice NemoClaw` |
   | `features.bot_user.display_name` | `MyUser NemoClaw Staging` | `Alice NemoClaw` |
   | `features.slash_commands[].command` | `/myuser-nemoclaw` | `/alice-nemoclaw` |

   The slash command must be lowercase and hyphen-separated. Note it down — you'll
   see this name appear in Slack when users type `/`.

3. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
4. Choose **From an app manifest**, select your workspace, then click **Next**.
5. Paste your edited manifest JSON and click **Next**, review the permissions, then click **Create**.

The manifest configures:

- Socket Mode (no public URL required)
- Bot events: `message.im`, `message.channels`, `message.mpim`, `app_mention`
- OAuth scopes: `im:history`, `im:read`, `channels:history`, `chat:write`,
  `reactions:write`, `users:read`, and related DM/channel permissions
- Your custom slash command (e.g. `/alice-nemoclaw`)

### 2b. Enable Socket Mode

1. In your new app's settings, go to **Socket Mode** in the left sidebar.
2. Toggle **Enable Socket Mode** on.
3. When prompted, name the app-level token (e.g. `nemoclaw-socket`) and click
   **Generate**. Copy the token — it starts with `xapp-`.

   Note - you may need to toggle socket mode off, then back on.

### 2c. Install the app to your workspace

1. Go to **OAuth & Permissions** in the left sidebar.
2. Click **Install to Workspace** and authorize it.
3. Copy the **Bot User OAuth Token** — it starts with `xoxb-`.

### 2d. Find your Slack user ID

The sandbox will only respond to messages from users on the allowlist.

1. Open Slack, click your name/avatar.
2. Click **Profile**, then the **⋮** (more) menu, then **Copy member ID**.
3. Save this — it looks like `U0887Q5UVV4`.

---

## 3. Get Your NVIDIA API Key

NemoClaw uses NVIDIA's inference endpoint for the agent's LLM.

1. Go to [build.nvidia.com](https://build.nvidia.com) and sign in with your NVIDIA
   account.
2. Navigate to any model page (e.g. Nemotron Super) and click **Get API Key**.
3. Copy the key — it starts with `nvapi-`.

---

## 4. Configure `.env`

Copy the template and fill in your values:

```bash
cp env.template .env
```

Open `.env` and fill in the following. Leave Outlook fields blank — they are not
required for this setup.

```ini
NEMOCLAW_AGENT=hermes
NEMOCLAW_PROVIDER=compatible-endpoint
NEMOCLAW_ENDPOINT_URL=https://integrate.api.nvidia.com/v1
COMPATIBLE_API_KEY=nvapi-<your key from build.nvidia.com>
NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b
NEMOCLAW_POLICY_MODE=custom

SLACK_BOT_TOKEN=xoxb-<your bot token from OAuth & Permissions>
SLACK_APP_TOKEN=xapp-<your app-level token from Socket Mode>
SLACK_ALLOWED_IDS=<your Slack user ID, e.g. U0887Q5UVV4>

GITHUB_TOKEN=ghp_<optional: a GitHub personal access token>

NEMOCLAW_SANDBOX_NAME=nemoclaw-hermes
NEMOCLAW_POLICY_PRESETS=npm,pypi,huggingface,brew,brave,slack,github,nvidia-forum,wttr
```

> **Note on `SLACK_ALLOWED_IDS`:** Only the user IDs listed here can message the bot.
> Add multiple IDs as a comma-separated list. This is the primary access control —
> keep it to individuals who should have agent access.
>
> **Note on `GITHUB_TOKEN`:** Optional. If set, the agent can use `gh` to query
> GitHub issues and PRs. Create a classic PAT at [github.com/settings/tokens](https://github.com/settings/tokens)
> with `repo` scope. **NVIDIA org requirement:** after creating the token you must
> authorize it for SAML SSO — on the token list page click **Configure SSO** next to
> the token and click **Authorize** next to the NVIDIA organization. Without this step
> the agent will get a "Resource protected by organization SAML enforcement" error on
> any NVIDIA org repository, even with a valid token.

---

## 5. Deploy Observability System (Optional)

NemoClaw integrates with [Arize Phoenix](https://arize.com/docs/phoenix) for agent
telemetry. When enabled, every conversation produces an OpenTelemetry trace with spans
for the LLM call, each tool invocation, and the overall session — visible in the
Phoenix UI in real time.

This step is optional. Skip it if you don't need trace-level observability.

### 5a. Start Phoenix

In a separate terminal, pull and run the Phoenix container:

```bash
docker pull arizephoenix/phoenix:latest
docker run --rm -p 6006:6006 -p 4317:4317 arizephoenix/phoenix:latest
```

Phoenix exposes two ports:

- **6006** — web UI and OTLP/HTTP trace ingestion (`/v1/traces`)
- **4317** — OTLP/gRPC trace ingestion (not used by NemoClaw)

Once started, the UI is available at [http://localhost:6006](http://localhost:6006).

### 5b. Configure NemoClaw to send traces

Add the following to your `.env`:

```ini
PHOENIX_COLLECTOR_ENDPOINT=http://172.17.0.1:6006/v1/traces
```

`172.17.0.1` is the Docker bridge IP — the address the sandbox container uses to
reach services on the host. If your Docker bridge is on a different subnet, replace
it with the correct IP (`ip addr show docker0` to check).

> **Note:** Phoenix telemetry requires the NeMo-Flow patched Hermes base image, which
> is built automatically when the `third_party/nemo-flow` submodule is present. If
> the submodule is not initialized, this variable is ignored.

### 5c. Rebuild and verify

Rebuild the sandbox to pick up the new endpoint:

```bash
set -a && source .env && set +a && node bin/nemoclaw.js <sandbox-name> rebuild --yes
```

Send a message to your bot in Slack, then open [http://localhost:6006](http://localhost:6006).
Under **Projects → default**, you should see a new trace for each conversation turn
with child spans for the LLM call and any tools the agent used.

---

## 6. Run Onboard

Source `.env` before running — the NemoClaw CLI reads all configuration from
`process.env` and does not load `.env` automatically. The `set -a` flag is
required so variables are exported to child processes (plain `source .env`
sets shell variables but does not export them to `node`).

```bash
set -a && source .env && set +a && node bin/nemoclaw.js onboard --non-interactive
```

This will:

1. Pull the base sandbox image
2. Build a sandbox container image with your configuration baked in
3. Push it to the local OpenShell gateway
4. Apply the network policy presets
5. Start the sandbox and connect to Slack

The first run takes 3–5 minutes. Subsequent rebuilds are faster because the base
image is cached.

To rebuild after changing `.env` or any agent file:

```bash
set -a && source .env && set +a && node bin/nemoclaw.js nemoclaw-hermes rebuild --yes
```

---

## 7. Verify

Once onboard completes, open Slack and send a direct message to your bot. It should
respond within a few seconds. If there is no response after 30 seconds, check logs:

```bash
node bin/nemoclaw.js nemoclaw-hermes logs --follow
```

The most common startup issue is a policy race: the sandbox starts and tries to
connect to Slack before policies have finished loading. This resolves automatically —
the gateway retries the connection and succeeds once policy version 7 (slack) is active.

---

## What's Included

### Network Policy Presets

The `NEMOCLAW_POLICY_PRESETS` value in `.env` controls which external services the
sandbox agent is allowed to reach. Each preset is a named YAML file in
`nemoclaw-blueprint/policies/presets/`.

| Preset | What it opens | Notes |
|--------|--------------|-------|
| `slack` | `slack.com`, `api.slack.com`, Socket Mode WebSocket | Required for Slack integration |
| `github` | `github.com`, `api.github.com` | Enables `gh` CLI and `git`; requires `GITHUB_TOKEN` |
| `npm` | `registry.npmjs.org`, `registry.yarnpkg.com` | Package installs via npm/yarn |
| `pypi` | `pypi.org`, `files.pythonhosted.org` | Package installs via pip/uv |
| `huggingface` | `huggingface.co`, LFS CDN, inference router | Model downloads and HF inference |
| `brew` | `formulae.brew.sh`, GitHub, container registries | Linuxbrew package installs |
| `brave` | `api.search.brave.com` | Web search via Brave Search API |
| `nvidia-forum` | `forums.developer.nvidia.com`, `docs.nvidia.com` | NVIDIA Developer Forums and docs |
| `wttr` | `wttr.in` | Weather lookups |

To remove a preset, delete it from the `NEMOCLAW_POLICY_PRESETS` list and rebuild.
The sandbox cannot reach any host not covered by an active preset.

### Agent Soul

The agent's system prompt (`agents/hermes/SOUL.md`) sets the sandbox context:

- The agent knows it runs inside an OpenShell sandbox with a strict egress policy
- When a network request is blocked (HTTP 403 from the proxy), the agent reports
  this to the user rather than retrying with different tools
- Tool guidance is included for GitHub (`gh` CLI), Slack channel reading, NVIDIA
  forums, and weather

### Agent Skills

Skills are loaded on demand by the agent when relevant to a task. They live in
`agents/hermes/skills/`.

**`github-interactions`** — Teaches the agent why `curl` is blocked for GitHub API
calls and how to use the `gh` CLI instead. Includes examples for listing issues, PRs,
and fetching API data. Load this skill when doing any GitHub work.

**`slack-channel-summarizer`** — Step-by-step procedure for reading and summarizing
Slack channel history using the Slack Web API via the authenticated bot token. Handles
pagination, user ID resolution, and time-range filtering.

**`nvidia-forum-search`** — Searches the NVIDIA Developer Forums JSON endpoint with
strict rate-limit guardrails: one attempt per search term, two searches maximum per
task, immediate stop on any 429 response. Prevents the agent from spiraling on
throttled requests.
