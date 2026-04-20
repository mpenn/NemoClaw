---
title:
  page: "Set Up Outlook with NemoClaw and Hermes"
  nav: "Set Up Outlook"
description:
  main: "Connect Microsoft Outlook to your sandboxed Hermes agent using a Python sidecar bridge that polls Microsoft Graph API and relays messages through OpenShell-managed credential injection."
  agent: "Explains how Outlook email reaches the sandboxed Hermes agent via a Python sidecar bridge (outlook-bridge.py) that polls the Microsoft Graph API, relays message bodies to the Hermes HTTP API, and sends replies. Use when setting up Outlook email integration, scheduled email jobs, or any Microsoft Graph-based messaging workflow."
keywords: ["nemoclaw outlook", "outlook bridge hermes agent", "microsoft graph openshell", "email agent nemoclaw"]
topics: ["generative_ai", "ai_agents"]
tags: ["hermes", "openshell", "outlook", "microsoft-graph", "deployment", "nemoclaw"]
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

The Outlook channel uses a Python sidecar bridge (`outlook-bridge.py`) rather than a native Hermes platform.
The bridge starts automatically alongside the Hermes gateway when Outlook is configured during `nemoclaw onboard`.
It polls the monitored mailbox via Microsoft Graph API every 30 seconds, relays each email to the agent, and sends the reply back to the sender.

Credentials never appear in the Docker image or environment files.
The bridge embeds OpenShell placeholder strings (for example `openshell:resolve:env:OUTLOOK_TENANT_ID`) directly in HTTP request URLs, and the OpenShell L7 proxy rewrites them with real values at egress.

## Prerequisites

- A machine where you can run `nemoclaw onboard` (local or remote host that runs the gateway and sandbox).
- An Azure Active Directory tenant with permission to register an application.
- An admin who can grant application-level Graph API permissions (or you have that permission yourself).

## Register an Azure Application

1. Open [portal.azure.com](https://portal.azure.com) and navigate to **Azure Active Directory → App registrations → New registration**.
2. Give the app a name (for example `NemoClaw Hermes Bridge`), leave the redirect URI blank, and click **Register**.
3. On the **Overview** page, copy:
   - **Application (client) ID** → this is `OUTLOOK_CLIENT_ID`
   - **Directory (tenant) ID** → this is `OUTLOOK_TENANT_ID`
4. Navigate to **Certificates & secrets → New client secret**, add a description, choose an expiry, and click **Add**.
   Copy the **Value** immediately (it is only shown once) → this is `OUTLOOK_CLIENT_SECRET`.
5. Navigate to **API permissions → Add a permission → Microsoft Graph → Application permissions** and add:
   - `Mail.Read`
   - `Mail.Send`
   - `Mail.ReadWrite`
6. Click **Grant admin consent for \<your org\>** and confirm.
   The permissions must show a green **Granted** status before the bridge can acquire tokens.

The mailbox the bridge monitors is set separately — any valid user email address in the tenant works.
That address is `OUTLOOK_USER_EMAIL`.

## Provide Credentials and Optional Sender Allowlist

Onboarding reads Outlook credentials from either host environment variables or the NemoClaw credential store.
You do not need to export variables if you enter them when the wizard asks.

### Option A: Environment variables (CI, scripts, or before you start the wizard)

```console
$ export OUTLOOK_CLIENT_ID=<application-client-id>
$ export OUTLOOK_TENANT_ID=<directory-tenant-id>
$ export OUTLOOK_CLIENT_SECRET=<client-secret-value>
$ export OUTLOOK_USER_EMAIL=<mailbox@yourdomain.com>
```

Optional comma-separated sender allowlist (the bridge ignores email from addresses not on this list):

```console
$ export NEMOCLAW_OUTLOOK_ALLOWED_SENDERS="alice@example.com,bob@example.com"
```

Leave `NEMOCLAW_OUTLOOK_ALLOWED_SENDERS` unset or blank to accept email from any sender.

### Option B: Interactive `nemoclaw onboard`

When the wizard reaches **Messaging channels**, it lists Telegram, Discord, Slack, and Outlook.
Press **4** to toggle Outlook on, then **Enter** when done.
The wizard then prompts for each credential in sequence and saves them to the credential store.
It also asks for the optional sender allowlist — leave blank to accept all senders.

## Run `nemoclaw onboard`

Complete the rest of the wizard so the blueprint can:

- Create an OpenShell provider (`<sandbox>-outlook-bridge`) with the four credentials.
- Bake `outlook` into the channel list (`NEMOCLAW_MESSAGING_CHANNELS_B64`).
- Bake the allowed-senders list as the `NEMOCLAW_OUTLOOK_ALLOWED_SENDERS` image environment variable.
- Build the sandbox image and start the gateway.

The bridge starts automatically once the Hermes gateway is healthy.
Logs are written to `/tmp/outlook-bridge.log` inside the sandbox.

If you change credentials or toggle the channel after a sandbox already exists, run `nemoclaw onboard` again so the image and provider attachments are rebuilt.

## Confirm Delivery

After the sandbox is running, send an email to `OUTLOOK_USER_EMAIL` from an allowed address.
Within approximately 30 seconds a reply from the agent should arrive in your inbox.

To inspect bridge activity inside the sandbox:

```console
$ openshell term
# Inside the sandbox:
$ tail -f /tmp/outlook-bridge.log
```

If the bridge does not start, verify that:

- `openshell sandbox policy` shows `graph.microsoft.com` and `login.microsoftonline.com` as allowed.
- The Azure app has **Granted** application permissions (not delegated).
- `openshell provider list` shows `<sandbox>-outlook-bridge` with all four credentials.

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
