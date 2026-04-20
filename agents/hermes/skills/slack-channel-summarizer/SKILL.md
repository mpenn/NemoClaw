# slack-channel-summarizer

Read and summarize messages from a Slack channel using the Slack Web API.

## When to use

- Summarize recent activity in a channel
- Review conversation history for a time range
- Track participation and themes across messages

## Prerequisites

- `SLACK_BOT_TOKEN` accessible as `openshell:resolve:env:SLACK_BOT_TOKEN` (the bot must be
  invited to the channel before it can read messages)
- The bot needs the `channels:history`, `channels:read`, `users:read` OAuth scopes

## Procedure

### 1. Find the channel ID

Prefer `users.conversations` over `conversations.list` — it is faster and does not
require the `channels:read` scope for private channels:

```bash
curl -s "https://api.slack.com/api/users.conversations?types=public_channel,private_channel&limit=200" \
     -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN" \
  | python3 -c "import sys,json; [print(c['id'], c['name']) for c in json.load(sys.stdin)['channels']]"
```

### 2. Fetch messages (newest-first)

Use `conversations.history`. Messages are returned newest-first; paginate
with `cursor` when there are more than 200 messages in your window:

```bash
curl -s "https://api.slack.com/api/conversations.history?channel=CHANNEL_ID&limit=50" \
     -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN"
```

To restrict to a time range, add `oldest=<unix_ts>` and/or `latest=<unix_ts>`.

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

## Pitfalls

- `conversations.list` with `types=private_channel` requires the `groups:read` scope and
  is slow on large workspaces — prefer `users.conversations` (step 1).
- The bot must be **invited** to a channel before it can read its history.
- `search.messages` requires a **user** token, not a bot token — it will not work here.
- Messages are returned **newest-first**; reverse the array before summarising
  chronologically.
- For channels with more than 200 messages, loop using the `response_metadata.next_cursor`
  field until it is empty.
- Use the placeholder string `openshell:resolve:env:SLACK_BOT_TOKEN` literally in the
  `Authorization` header — do not substitute the real token value.
