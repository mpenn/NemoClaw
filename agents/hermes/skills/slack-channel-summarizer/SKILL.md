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
- The bot needs the `channels:history`, `channels:read`, `users:read` OAuth scopes
- The sandbox Slack policy permits `curl` to the Slack Web API

## Procedure

### 1. Fetch messages (newest-first)

If you already know the channel ID (e.g., from a Slack mention like `<#C0ALN454EH4>`),
use it directly — skip the lookup below.

Use `conversations.history`. Messages are returned newest-first; paginate
with `cursor` when there are more than 200 messages in your window:

```bash
curl -s "https://api.slack.com/api/conversations.history?channel=CHANNEL_ID&limit=50" \
     -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN"
```

To restrict to a time range, add `oldest=<unix_ts>` and/or `latest=<unix_ts>`.

If you do **not** know the channel ID, find it first with `users.conversations`:

```bash
curl -s "https://api.slack.com/api/users.conversations?types=public_channel,private_channel&limit=200" \
     -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN" \
  | python3 -c "import sys,json; [print(c['id'], c['name']) for c in json.load(sys.stdin)['channels']]"
```

### 2. Resolve user IDs to display names

Message objects contain `user` fields with opaque IDs (e.g. `U01AB2CD3`).
Resolve them one at a time with `users.info`:

```bash
curl -s "https://api.slack.com/api/users.info?user=U01AB2CD3" \
     -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN" \
  | python3 -c "import sys,json; u=json.load(sys.stdin)['user']; print(u['real_name'])"
```

### 3. Present the summary

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

- `conversations.list` with `types=private_channel` requires the `groups:read` scope and
  is slow on large workspaces — prefer `users.conversations` if you need to look up an ID.
- The bot must be **invited** to a channel before it can read its history.
- `search.messages` requires a **user** token, not a bot token — it will not work here.
- `users.conversations` requires the `groups:read` scope to return private channels; if
  the call fails with `missing_scope`, use the channel ID from the Slack mention directly.
- Messages are returned **newest-first**; reverse the array before summarising
  chronologically.
- **Fetch at most 3 pages** (≤ 150 messages total with `limit=50`). Stop after 3 pages
  regardless of whether `next_cursor` is present. Summarise what you have — do not
  paginate indefinitely. If the user asked for a specific time range use `oldest=` and
  `latest=` to target it directly instead of paginating forward from now.
- Use the placeholder string `openshell:resolve:env:SLACK_BOT_TOKEN` literally in the
  `Authorization` header — do not substitute the real token value.
