# NemoClaw Hermes Setup Guide (NVIDIA Internal)

This guide walks through standing up a NemoClaw Hermes sandbox from source with the
policy set and credentials needed for Slack, GitHub, and NVIDIA forum research.
It also notes the current Outlook bridge path, which is still a WIP and should be
treated as optional. It covers building from source, creating the Slack app,
configuring credentials, and running the first onboard.

> **Important**
>
> Run `git submodule update --init --recursive` immediately after cloning and before
> any build or onboard step. The NeMo-Flow Hermes telemetry path is gated on the
> `third_party/nemo-flow` submodule contents, not just on the parent repo commit.
> If the submodule is not initialized, onboard falls back to the standard Hermes
> base image and skips the patched NeMo-Flow image path.

---

## Prerequisites

- Linux host (Ubuntu 22.04+ recommended) with Docker installed and running
- Access to [NVIDIA API Catalog](https://build.nvidia.com) for a compatible-endpoint inference API key
- A Slack workspace where you have permission to create apps
- Node.js 22.16+ and npm
- Python 3.11+ and `uv`
- `git`

Verify the host actually meets those tool prerequisites before starting:

```bash
node --version
command -v uv
```

If your host `node` is older than 22.x, or `uv` is missing from `PATH`, fix that
first. The source build and onboard flow assume both are available.

---

## 1. Clone and Build from Source

```bash
git clone https://github.com/mpenn/NemoClaw.git
cd NemoClaw
git checkout community-sentiment-issue-tracker-demo
git submodule update --init --recursive
```

If `third_party/nemo-flow/patches/hermes-agent/0001-add-nemo-flow-integration.patch`
is missing after clone, the submodule is not initialized correctly and the telemetry
build path will be skipped.

Verify you are actually running Node 22 for the CLI. If your host `node` is older,
use the `npx`-resolved binary for all npm-backed repo steps and NemoClaw commands:

```bash
node --version
NODE22=$(npx -y node@22 -p 'process.execPath')
"$NODE22" ./bin/nemoclaw.js --version
```

Install all dependencies and build from source:

```bash
"$NODE22" "$(command -v npm)" install
cd nemoclaw && "$NODE22" "$(command -v npm)" install && "$NODE22" "$(command -v npm)" run build && cd ..
cd nemoclaw-blueprint && uv sync && cd ..
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
   | `display_information.name` | `MyUser NemoClaw` | `Alice NemoClaw` |
   | `features.bot_user.display_name` | `MyUser NemoClaw` | `Alice NemoClaw` |
   | `features.slash_commands[].command` | `/myuser-nemoclaw` | `/alice-nemoclaw` |

   The slash command must be lowercase and hyphen-separated. Note it down because
   you will see this name appear in Slack when users type `/`.

3. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
4. Choose **From an app manifest**, select your workspace, then click **Next**.
5. Paste your edited manifest JSON and click **Next**, review the permissions, then click **Create**.

The manifest configures:

- Socket Mode (no public URL required)
- Bot events: `message.im`, `message.channels`, `message.mpim`, `app_mention`
- OAuth scopes: `im:history`, `im:read`, `channels:history`, `chat:write`,
  `reactions:write`, `users:read`, and related DM/channel permissions
- Your custom slash command (for example `/alice-nemoclaw`)

### 2b. Enable Socket Mode

1. In your new app's settings, go to **Socket Mode** in the left sidebar.
2. Toggle **Enable Socket Mode** on.
3. When prompted, name the app-level token (for example `nemoclaw-socket`) and click
   **Generate**. Copy the token. It starts with `xapp-`.

   Note: if Slack behaves oddly here, toggle Socket Mode off and back on once.

### 2c. Install the app to your workspace

1. Go to **OAuth & Permissions** in the left sidebar.
2. Click **Install to Workspace** and authorize it.
3. Copy the **Bot User OAuth Token**. It starts with `xoxb-`.

### 2d. Find your Slack user ID

The sandbox only responds to users on the allowlist.

1. Open Slack and click your name or avatar.
2. Click **Profile**, then the **⋮** menu, then **Copy member ID**.
3. Save this. It looks like `U0887Q5UVV4`.

---

## 3. Get Your NVIDIA API Key

NemoClaw uses a compatible OpenAI-style endpoint for the agent's LLM. The default
template is wired to the NVIDIA integrate endpoint.

1. Go to [build.nvidia.com](https://build.nvidia.com) and sign in with your NVIDIA
   account.
2. Navigate to any model page and click **Get API Key**.
3. Copy the key. It starts with `nvapi-`.

---

## 4. Configure `.env`

Copy the template and fill in your values:

```bash
cp env.template .env
```

Open `.env` and fill in the following. Leave the Outlook fields blank unless you are
explicitly testing the Outlook bridge WIP path.

```ini
NEMOCLAW_AGENT=hermes
NEMOCLAW_PROVIDER=compatible-endpoint
NEMOCLAW_ENDPOINT_URL=https://integrate.api.nvidia.com/v1
COMPATIBLE_API_KEY=nvapi-<your key from build.nvidia.com>
NEMOCLAW_PROVIDER_KEY=nvapi-<same key as above>
# NVIDIA_API_KEY=nvapi-<optional legacy variable; not used by this guide's compatible-endpoint flow>
NEMOCLAW_MODEL=qwen/qwen3-next-80b-a3b-instruct
NEMOCLAW_POLICY_MODE=custom

SLACK_BOT_TOKEN=xoxb-<your bot token from OAuth & Permissions>
SLACK_APP_TOKEN=xapp-<your app-level token from Socket Mode>
SLACK_ALLOWED_IDS=<your Slack user ID, e.g. U0887Q5UVV4>

GITHUB_TOKEN=ghp_<optional: a GitHub personal access token>

OUTLOOK_TENANT_ID=<optional: Microsoft tenant id>
OUTLOOK_CLIENT_ID=<optional: Microsoft app client id>
OUTLOOK_CLIENT_SECRET=<optional: Microsoft app client secret>
OUTLOOK_BOT_MAILBOX=<optional: shared mailbox the bridge monitors>
OUTLOOK_USER_MAILBOX=<optional: your mailbox used for scheduled-job replies>

NEMOCLAW_SANDBOX_NAME=nemoclaw-hermes
NEMOCLAW_POLICY_PRESETS=slack,github,outlook,nvidia-forum

# Optional Phoenix telemetry endpoint. Leave unset unless you are enabling Phoenix.
# PHOENIX_COLLECTOR_ENDPOINT=http://172.17.0.1:6006/v1/traces
```

> **Note on `SLACK_ALLOWED_IDS`:** Only the user IDs listed here can message the bot.
> Add multiple IDs as a comma-separated list. This is the primary access control.
>
> **Note on `GITHUB_TOKEN`:** Optional. If set, the agent can use `gh` to query
> GitHub issues and PRs. Create a classic PAT at [github.com/settings/tokens](https://github.com/settings/tokens)
> with `repo` scope. After creating the token, authorize it for NVIDIA SAML SSO by
> clicking **Configure SSO** next to the token and then **Authorize** next to the
> NVIDIA organization.
>
> **Note on `NVIDIA_API_KEY`:** Some local `.env` files still carry `NVIDIA_API_KEY`
> from older or different inference flows. For this Hermes guide, the active path is
> `compatible-endpoint`, so `COMPATIBLE_API_KEY` and `NEMOCLAW_PROVIDER_KEY` are the
> variables that matter.

---

## 5. Deploy Observability System (Optional)

NemoClaw integrates with [Arize Phoenix](https://arize.com/docs/phoenix) for agent
telemetry. When enabled, each conversation produces an OpenTelemetry trace with spans
for the LLM call, each tool invocation, and the overall session.

This step is optional. Skip it if you do not need trace-level observability.

### 5a. Start Phoenix

In a separate terminal, pull and run the Phoenix container:

```bash
docker pull arizephoenix/phoenix:latest
docker run --rm -p 6006:6006 -p 4317:4317 arizephoenix/phoenix:latest
```

Phoenix exposes two ports:

- `6006` for the web UI and OTLP/HTTP trace ingestion at `/v1/traces`
- `4317` for OTLP/gRPC trace ingestion

### 5b. Configure NemoClaw to Send Traces

Add the following to `.env`:

```ini
PHOENIX_COLLECTOR_ENDPOINT=http://172.17.0.1:6006/v1/traces
```

`172.17.0.1` is the Docker bridge IP that the sandbox uses to reach services on the
host. If your Docker bridge uses a different subnet, replace it with the correct IP.

> **Note:** Phoenix telemetry requires the NeMo-Flow patched Hermes base image, which
> is only built when the `third_party/nemo-flow` submodule is initialized. If the
> submodule is not initialized, this variable is ignored.
>
> If you already onboarded before initializing the submodule, rerun a forced rebuild
> after the submodule update so NemoClaw actually builds and deploys the patched image.

### 5c. Rebuild and Verify

Rebuild the sandbox to pick up the new endpoint:

```bash
set -a && source .env && set +a
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
NODE22=$(npx -y node@22 -p 'process.execPath')
"$NODE22" ./bin/nemoclaw.js <sandbox-name> rebuild --yes
```

If you are iterating on the NeMo-Flow submodule or `Dockerfile.base.nemo-flow`, drop
the cached patched base image before rebuilding so Docker does not reuse an old local
base:

```bash
docker image rm ghcr.io/nvidia/nemoclaw/hermes-sandbox-base-nemo-flow:latest || true
```

Send a message to your bot in Slack, then open [http://localhost:6006](http://localhost:6006).
Under **Projects -> default**, you should see a new trace for each conversation turn.

---

## 6. Run Onboard

Source `.env` before running. The NemoClaw CLI reads all configuration from
`process.env` and does not load `.env` automatically. The `set -a` flag is required
so variables are exported to child processes. Use the explicit Node 22 binary if
your host default `node` is older.

```bash
set -a && source .env && set +a
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
NODE22=$(npx -y node@22 -p 'process.execPath')
"$NODE22" ./bin/nemoclaw.js onboard --non-interactive
```

This will:

1. Pull the base sandbox image
2. Build a sandbox container image with your configuration baked in
3. Push it to the local OpenShell gateway
4. Apply the network policy presets
5. Start the sandbox and attach the configured channel providers

The first run takes 3-5 minutes. Subsequent rebuilds are faster because the base
image is cached.

If the sandbox already exists, `onboard --non-interactive` may reuse it instead of
building a fresh image. For merged-branch validation, NeMo-Flow verification, or any
change that must be present in the built image, follow onboard with an explicit
rebuild:

```bash
set -a && source .env && set +a
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
NODE22=$(npx -y node@22 -p 'process.execPath')
"$NODE22" ./bin/nemoclaw.js nemoclaw-hermes rebuild --yes
```

During onboarding you may still see:

- `Configuring inference (NIM)` even when using the compatible-endpoint flow
- an initial Hermes or dashboard probe timeout before the sandbox becomes healthy
- the sandbox starting up before all preset policies are attached

Those messages are expected with the current onboard flow and do not necessarily
mean the install failed.

To rebuild after changing `.env` or any agent file:

```bash
set -a && source .env && set +a
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
NODE22=$(npx -y node@22 -p 'process.execPath')
"$NODE22" ./bin/nemoclaw.js nemoclaw-hermes rebuild --yes
```

---

## 7. Verify

Once onboard completes, open Slack and send a direct message to your bot. It should
respond within a few seconds. If there is no response after 30 seconds, check logs:

```bash
NODE22=$(npx -y node@22 -p 'process.execPath')
"$NODE22" ./bin/nemoclaw.js nemoclaw-hermes logs --follow
```

The most common startup issue is a policy race: the sandbox starts and tries to
connect to Slack before the preset policies have finished loading. This resolves
automatically once the preset set is attached. You may also see an onboarding warning
that Hermes did not respond to the initial 90 second health probe even though the
sandbox becomes healthy shortly afterward.

---

## What's Included

### Network Policy Presets

The `NEMOCLAW_POLICY_PRESETS` value in `.env` controls which external services the
sandbox agent is allowed to reach. Each preset is a named YAML file in
`nemoclaw-blueprint/policies/presets/`.

| Preset | What it opens | Notes |
|--------|--------------|-------|
| `slack` | `slack.com`, `api.slack.com`, `hooks.slack.com`, Socket Mode WebSocket | Required for Slack integration and Slack Web API research |
| `github` | `github.com`, `api.github.com` | Enables `gh` CLI and `git`; requires `GITHUB_TOKEN` |
| `outlook` | `graph.microsoft.com`, `login.microsoftonline.com` | Optional WIP Outlook bridge path; not required for the main Slack/GitHub/forum workflow |
| `nvidia-forum` | `forums.developer.nvidia.com`, `docs.nvidia.com` | NVIDIA Developer Forums and docs |

To remove a preset, delete it from `NEMOCLAW_POLICY_PRESETS` and rebuild. The sandbox
cannot reach any host not covered by an active preset.

### Agent Soul

The agent's system prompt (`agents/hermes/SOUL.md`) sets the sandbox context:

- The agent knows it runs inside an OpenShell sandbox with a strict egress policy
- When a network request is blocked, the agent reports that to the user rather than
  retrying with different tools
- It keeps response behavior and skill-routing high level rather than embedding
  detailed source-specific procedures in the main prompt

### Agent Skills

Skills are loaded on demand by the agent when relevant to a task. They live in
`agents/hermes/skills/`.

- `github-interactions` for GitHub reads and repo operations
- `slack-channel-summarizer` for Slack channel resolution and message history
- `nvidia-forum-search` for NVIDIA Developer Forum access
- `cross-source-gap-analysis` for comparing Slack, GitHub, and forum findings
