---
name: slack-channel-summarizer
description: Read, summarize, and compare Slack channel history using the Slack Web API.
---

# slack-channel-summarizer

Read, summarize, and compare messages from a Slack channel using the Slack Web API.

## When to use

- Summarize recent activity in a channel
- Review conversation history for a time range
- Track participation and themes across messages
- Compare Slack discussion against GitHub issues, PRs, or forum discussion

## Prerequisites

- `SLACK_BOT_TOKEN` accessible as `openshell:resolve:env:SLACK_BOT_TOKEN` (the bot must be
  invited to the channel before it can read messages)
- Public-channel discovery requires `channels:read`
- Public-channel history requires `channels:history`
- Private-channel discovery requires `groups:read`
- Private-channel history requires `groups:history`
- `users:read` is needed if you want to resolve user IDs to names
- The sandbox Slack policy permits `curl` to the Slack Web API

Assume this access path is available in the Hermes sandbox when the Slack
channel is configured. Do not ask the user to confirm that you may use Slack
history or the Slack Web API before trying the documented flow below.

Do not use `session_search` to discover a Slack channel ID unless the user
explicitly says the ID was shared in a prior conversation. Use Slack APIs or a
direct Slack mention/link from the user.

## Hard rules

- Never start channel discovery with `users.conversations?types=public_channel,private_channel`.
- Never treat `missing_scope: groups:read` as proof that Slack research is unavailable.
- If the user gives only a channel name, you must try paginated public-channel
  discovery first.
- Only attempt private-channel discovery after the public-channel pass fails.
- If private-channel discovery fails with `missing_scope: groups:read`, ask for
  a direct Slack mention or URL. Do not say channel history is inaccessible in general.
- If you already have a channel ID, go straight to `conversations.history`.
- Do not use `session_search` as a substitute for Slack API discovery.
- Use the resolver script below for channel-name lookup. Do not reimplement the
  lookup logic ad hoc with raw Slack API calls unless you are debugging the script itself.
- If the user gives only a channel name, your first command should be this exact pattern:
  `python3 /sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py --name 'CHANNEL_NAME'`

## Resolver script

Use this helper script for Slack channel resolution:

`/sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py`

Examples:

```bash
python3 /sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py --input '#nemoclaw-situation-room'
python3 /sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py --input '<#C0ALN454EH4>'
python3 /sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py --name 'nemoclaw-situation-room'
```

The script prints JSON. Important outcomes:

- `{"ok": true, "stage": "direct_id", "channel_id": "..."}`
  An ID was supplied directly or extracted from a mention/URL.
- `{"ok": true, "stage": "public_lookup", "channel_id": "..."}`
  The channel was found via paginated public-channel lookup.
- `{"ok": true, "stage": "private_lookup", "channel_id": "..."}`
  The channel was found via paginated private-channel lookup.
- `{"ok": false, "error": "missing_private_discovery_scope", "needed": "groups:read", ...}`
  Public lookup did not find the channel, and private discovery is blocked by missing scope.
- `{"ok": false, "error": "channel_not_found", ...}`
  The channel was not found in the allowed lookup paths.

If the resolver returns `missing_private_discovery_scope`, ask the user for a
direct Slack mention or channel URL. Do not claim Slack research is unavailable
in general.

## Procedure

### 1. Resolve the channel ID

#### Best case: the user already gave you the ID

If the user includes a Slack channel mention like `<#C0ALN454EH4>` or a Slack
channel URL containing the ID, extract the `C...` or `G...` ID and skip
discovery entirely.

If you already know the channel ID (e.g., from a Slack mention like `<#C0ALN454EH4>`),
use it directly — skip the lookup below.

If the user gives only a channel name such as `nemoclaw-situation-room`, resolve
it with the resolver script first. Do not stop to ask the user to confirm the channel
ID first, and do not replace this step with ad hoc Slack API discovery commands.

```bash
python3 /sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py --name 'nemoclaw-situation-room'
```

Decision rule:

- If the script returns `ok: true`, use the returned `channel_id`.
- If the script returns `missing_private_discovery_scope`, ask for a direct
  Slack mention or channel URL and say that public-channel lookup was already attempted.
- If the script returns `channel_not_found`, say the channel could not be found
  through the allowed lookup paths.

### 2. Fetch messages (newest-first)

Use `conversations.history`. Messages are returned newest-first; paginate
only when necessary:

```bash
curl -s "https://api.slack.com/api/conversations.history?channel=CHANNEL_ID&limit=15" \
     -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN"
```

To restrict to a time range, add `oldest=<unix_ts>` and/or `latest=<unix_ts>`.

Interpret common failures explicitly:

- `missing_scope` with `needed=channels:history`
  The bot can discover the public channel but cannot read its history.
- `missing_scope` with `needed=groups:history`
  The bot may know the private channel ID but lacks permission to read private-channel history.
- `not_in_channel`
  The bot is not actually a member of that channel.
- `channel_not_found`
  The ID is wrong, unavailable to the token, or the channel could not be resolved.

If `conversations.history` works, proceed. Do not ask the user for confirmation first.

If `conversations.history` fails with `not_in_channel`, say the bot is not in
that channel. If it fails with `missing_scope`, say which history scope is
missing. If it fails with `channel_not_found`, say the ID could not be resolved.
Do not collapse these into a generic “no access” statement.

### 3. Resolve user IDs to display names

Message objects contain `user` fields with opaque IDs (e.g. `U01AB2CD3`).
Resolve them one at a time with `users.info`:

```bash
curl -s "https://api.slack.com/api/users.info?user=U01AB2CD3" \
     -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN" \
  | python3 -c "import sys,json; u=json.load(sys.stdin)['user']; print(u['real_name'])"
```

### 4. Present the summary

Organise the output as a structured summary:
- Date range covered
- Key themes / topics discussed
- Active participants (resolved names)
- Any action items or decisions

If the user asked for comparison or gap analysis, extend the output with:
- What Slack is discussing that is not represented in GitHub or forum results
- What GitHub or forum items appear to be missing from Slack discussion
- Clear candidate follow-ups or issue areas to investigate

## Pitfalls

- Do not bypass the resolver script for channel-name lookup unless you are
  explicitly debugging the script itself.
- The resolver already searches public channels deeply by default. Do not fall back
  to a mixed `users.conversations?types=public_channel,private_channel` lookup just
  because the name was not found immediately.
- The exact failure to avoid is:
  calling `users.conversations?types=public_channel,private_channel`,
  receiving `missing_scope: groups:read`,
  then claiming the channel history cannot be read.
  That is wrong.
- Both `conversations.list` and `users.conversations` are paginated. A single
  page is not enough on large workspaces. Search up to 3 pages before giving up.
- The bot must be **invited** to a channel before it can read its history.
- `search.messages` requires a **user** token, not a bot token — it will not work here.
- `users.conversations` failing with `missing_scope: groups:read` does not mean
  Slack access is generally broken. It only blocks private-channel discovery by name.
- If the channel ID is already known, go straight to `conversations.history`.
  That is the cleanest way to distinguish missing history scope, missing membership,
  and an invalid ID.
- Messages are returned **newest-first**; reverse the array before summarising
  chronologically.
- Slack documents tighter limits for newer non-Marketplace apps. Start with
  `limit=15` and avoid deep pagination unless the user explicitly asks for it.
- For message reads, fetch at most 3 pages unless the user explicitly asks for a
  deeper pass. Prefer narrowing with `oldest=` / `latest=` over broad pagination.
- Use the placeholder string `openshell:resolve:env:SLACK_BOT_TOKEN` literally in the
  `Authorization` header — do not substitute the real token value.
