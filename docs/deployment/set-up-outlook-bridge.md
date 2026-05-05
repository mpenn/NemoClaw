---
title:
  page: "Set Up Outlook with NemoClaw and Hermes"
  nav: "Set Up Outlook"
description:
  main: "Connect Microsoft Outlook to your sandboxed Hermes agent using a delegated OAuth2 token manager and credential sidecar that injects live Microsoft Graph API tokens without baking credentials into the Docker image."
  agent: "Explains how Outlook email reaches the sandboxed Hermes agent via a Python sidecar bridge (outlook-bridge.py) that polls the Microsoft Graph API, relays message bodies to the Hermes HTTP API, and sends replies. The credential sidecar (ms_graph_sidecar.py) injects delegated OAuth tokens obtained from the MS Graph token manager running on the host. Use when setting up Outlook email integration, scheduled email jobs, or any Microsoft Graph-based messaging workflow."
keywords: ["nemoclaw outlook", "outlook bridge hermes agent", "microsoft graph delegated auth", "msal token manager", "email agent nemoclaw"]
topics: ["generative_ai", "ai_agents"]
tags: ["hermes", "openshell", "outlook", "microsoft-graph", "deployment", "nemoclaw", "delegated-auth", "msal"]
content:
  type: how_to
  difficulty: intermediate
  audience: ["developer", "engineer"]
status: published
---

<!--
  SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
  SPDX-License-Identifier: Apache-2.0
-->

# Set Up Outlook

The Outlook channel uses delegated OAuth2 authentication via the **MS Graph token manager**.
The token manager runs on the host machine and holds live MSAL sessions.
A credential sidecar (`ms_graph_sidecar.py`) runs inside the sandbox and injects a live delegated token whenever a bridge or skill sends `Authorization: Bearer MS_GRAPH_TOKEN_PLACEHOLDER_OUTLOOK` to `MS_GRAPH_SIDECAR_URL`.

Two mailboxes are involved:

- **`OUTLOOK_TARGET_MAILBOX`** — the agent's dedicated Entra account (for example `agt-you@nvidia.com`).
  The bridge polls this inbox for task requests and sends replies from it.
  This is also the account that signs in to the token manager.
- **`OUTLOOK_REPLY_TO`** — your personal mailbox (for example `you@nvidia.com`).
  Search skills use this address to read your mail via delegated `Mail.ReadWrite.Shared` access.

No credentials appear in the Docker image or environment files.
The session UUID (`OUTLOOK_SESSION_UUID`) is stored in an OpenShell provider and resolved by the L7 proxy at request time before reaching the token manager.

## Prerequisites

- A machine where you can run `nemoclaw onboard` (local or remote host that runs the gateway and sandbox).
- An Azure Active Directory tenant with permission to register an application.
- An admin who can grant delegated Graph API permissions (or you have that permission yourself).
- A dedicated Entra account for the agent (for example `agt-you@nvidia.com`) — this becomes `OUTLOOK_TARGET_MAILBOX`.
- Docker installed on the host machine (for the MS Graph token manager).

## Register an Azure Application

1. Open [portal.azure.com](https://portal.azure.com) and navigate to **Azure Active Directory → App registrations → New registration**.
2. Give the app a name (for example `NemoClaw Hermes`), leave the redirect URI blank for now, and click **Register**.
3. On the **Overview** page, copy:
   - **Application (client) ID** → this is `OUTLOOK_CLIENT_ID`
   - **Directory (tenant) ID** → this is `OUTLOOK_TENANT_ID`
4. Navigate to **Authentication → Add a platform → Web**, enter `http://localhost:51247` as the redirect URI, and click **Configure**.
5. Navigate to **API permissions → Add a permission → Microsoft Graph → Delegated permissions** and add:
   - `Mail.Read`
   - `Mail.Send`
   - `Mail.ReadWrite.Shared`
6. Click **Grant admin consent for \<your org\>** and confirm.
   The permissions must show a green **Granted** status before the bridge can acquire tokens.

No client secret is required — the delegated flow authenticates as the agent account, not as the application.

## Start the MS Graph Token Manager

The token manager is an MSAL OAuth server that holds delegated sessions and issues short-lived access tokens to the credential sidecar on demand.
Start it on the host machine before running `nemoclaw onboard`:

```console
$ cd extras/ms-graph-token-manager
$ docker compose up -d
```

It binds two ports:

| Port | Purpose |
|------|---------|
| `8765` | Token API — used by `authenticate.sh` and the credential sidecar |
| `51247` | OAuth redirect URI — receives the browser callback after sign-in |

Tokens are persisted in a named Docker volume and survive container restarts.
Re-authentication is only needed if the Entra refresh token expires (typically 90 days in corporate tenants) or if the agent account password changes.

## Authenticate and Obtain Session UUID

Run `authenticate.sh` as the **agent account** (`OUTLOOK_TARGET_MAILBOX`) to start the delegated auth flow and obtain a session UUID:

```console
$ OUTLOOK_SESSION_UUID=$(./extras/ms-graph-token-manager/scripts/authenticate.sh \
    --client-id "$OUTLOOK_CLIENT_ID" \
    --tenant-id "$OUTLOOK_TENANT_ID" \
    --login-hint agt-you@nvidia.com \
    --flow browser)
$ export OUTLOOK_SESSION_UUID
```

On a headless host, use the device code flow instead:

```console
$ OUTLOOK_SESSION_UUID=$(./extras/ms-graph-token-manager/scripts/authenticate.sh \
    --client-id "$OUTLOOK_CLIENT_ID" \
    --tenant-id "$OUTLOOK_TENANT_ID" \
    --login-hint agt-you@nvidia.com \
    --flow device)
```

The script prints `SESSION_ID=<uuid>` to stdout; all progress and browser URLs go to stderr.
The session UUID is stored in the token manager and reused across restarts — re-run the script only when the session expires or is invalidated.

To check whether an existing session is still valid:

```console
$ ./extras/ms-graph-token-manager/scripts/authenticate.sh \
    --client-id "$OUTLOOK_CLIENT_ID" \
    --tenant-id "$OUTLOOK_TENANT_ID" \
    --session-id "$OUTLOOK_SESSION_UUID"
```

## Provide Credentials and Optional Sender Allowlist

Onboarding reads Outlook credentials from either host environment variables or the NemoClaw credential store.
You do not need to export variables if you enter them when the wizard asks.

### Option A: Environment variables (CI, scripts, or before you start the wizard)

```console
$ export OUTLOOK_CLIENT_ID=<application-client-id>
$ export OUTLOOK_TENANT_ID=<directory-tenant-id>
$ export OUTLOOK_SESSION_UUID=<session-uuid-from-authenticate.sh>
$ export OUTLOOK_TARGET_MAILBOX=<agt-you@yourdomain.com>
$ export OUTLOOK_REPLY_TO=<you@yourdomain.com>
```

Optional comma-separated sender allowlist (the bridge ignores email from addresses not on this list):

```console
$ export NEMOCLAW_OUTLOOK_ALLOWED_SENDERS="alice@example.com,bob@example.com"
```

Leave `NEMOCLAW_OUTLOOK_ALLOWED_SENDERS` unset or blank to accept email from any sender.

### Option B: Interactive `nemoclaw onboard`

When the wizard reaches **Messaging channels**, press **4** to toggle Outlook on, then **Enter** when done.
The wizard prompts for each credential in sequence and saves them to the credential store.
It also asks for the optional sender allowlist — leave blank to accept all senders.

## Run `nemoclaw onboard`

Complete the rest of the wizard so the blueprint can:

- Create an OpenShell provider (`<sandbox>-outlook`) with `OUTLOOK_CLIENT_ID`, `OUTLOOK_TENANT_ID`, and `OUTLOOK_SESSION_UUID`.
- Bake `outlook` into the channel list (`NEMOCLAW_MESSAGING_CHANNELS_B64`).
- Bake the allowed-senders list as the `NEMOCLAW_OUTLOOK_ALLOWED_SENDERS` image environment variable.
- Set `OUTLOOK_TARGET_MAILBOX` and `OUTLOOK_REPLY_TO` in the sandbox environment.
- Build the sandbox image and start the gateway.

The bridge and credential sidecar start automatically once the Hermes gateway is healthy.
Logs are written to `/tmp/outlook-bridge.log` inside the sandbox.

If you change credentials or toggle the channel after a sandbox already exists, run `nemoclaw onboard` again so the image and provider attachments are rebuilt.

## Confirm Delivery

After the sandbox is running, send an email to `OUTLOOK_TARGET_MAILBOX` from an allowed address.
Within approximately 30 seconds a reply from the agent should arrive in your inbox.

To inspect bridge activity inside the sandbox:

```console
$ openshell term
# Inside the sandbox:
$ tail -f /tmp/outlook-bridge.log
```

If the bridge does not start, verify that:

- `openshell sandbox policy` shows `graph.microsoft.com` and `login.microsoftonline.com` as allowed.
- The Azure app has **Granted** delegated permissions (not Application).
- `openshell provider list` shows `<sandbox>-outlook` with `OUTLOOK_CLIENT_ID`, `OUTLOOK_TENANT_ID`, and `OUTLOOK_SESSION_UUID`.
- The MS Graph token manager is running on the host and reachable at `TOKEN_MANAGER_HOST:8765`.

## Renewing the Session

Entra refresh tokens typically expire after 90 days of inactivity in corporate tenants, or immediately if the agent account password changes or the app registration is modified.
When the session expires, the credential sidecar will log token fetch errors.

To renew:

1. Re-run `authenticate.sh` as the agent account — if the existing session is invalid it starts a new auth flow automatically.
2. Run `nemoclaw onboard` again to update `OUTLOOK_SESSION_UUID` in the OpenShell provider and rebuild the sandbox image.

## Scheduled Jobs

The bridge reads `/sandbox/.hermes-data/cron/outlook-jobs.json` at startup and schedules each entry using the `schedule` library.

### Job schema

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable label for logs |
| `time` | string | 24-hour `HH:MM` time to run daily |
| `prompt` | string | Prompt sent to the agent |
| `to` | string | Recipient email address for the result |
| `subject` | string | Email subject line |

### Example

```json
[
  {
    "name": "morning-report",
    "time": "10:00",
    "prompt": "Summarize the top AI research news from the past 24 hours.",
    "to": "team@example.com",
    "subject": "Daily AI Summary"
  }
]
```

Edit the file, then restart the bridge to pick up changes:

```console
$ openshell term
# Inside the sandbox:
$ kill $(pgrep -f outlook-bridge.py)
# The bridge does not restart automatically — restart the sandbox to reload jobs.
```

Alternatively, rebuild the sandbox with `nemoclaw onboard` to get a fresh job schedule.

## Related Topics

- [Deploy NemoClaw to a Remote GPU Instance](deploy-to-remote-gpu.md) for remote deployment with messaging.
- [Architecture](../reference/architecture.md) for how providers, the gateway, and the sandbox fit together.
- [Customize the Network Policy](../network-policy/customize-network-policy.md) for the `outlook` policy preset.
- [Set Up Telegram](set-up-telegram-bridge.md) for the native Hermes messaging channel approach.
