---
name: slack-channel-summarizer
description: Resolve Slack channels and read enough history and threads to answer questions from Slack evidence.
---

# slack-channel-summarizer

Use this skill when the answer depends on Slack channel history. If the user
asks about a topic but does not name a channel, use `slack-channel-finder`
first, then read the most likely channels.

## Access

- Token: `openshell:resolve:env:SLACK_BOT_TOKEN`.
- The bot can read only channels it can access.
- Resolver script:
  `/sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py`

## Resolve

Direct Slack mentions like `<#C0ALN454EH4>` already contain the channel ID.
For names or URLs, use:

```bash
python3 /sandbox/.hermes-data/skills/slack-channel-summarizer/scripts/resolve_slack_channel.py --input 'CHANNEL'
```

If private discovery scope is missing, ask for a direct channel mention or URL.
Do not claim Slack itself is unavailable unless the API call actually fails.

## Research Approach

- Read broadly enough to answer the question, not just the newest message.
- Use `limit=50` for ordinary research and `limit=100` for busy or weekly
  windows.
- Add `oldest=` and `latest=` when the user gives a time range.
- Fetch `conversations.replies` when a message has replies and appears relevant.
- For topic research, broaden across likely channels, terms, and time windows
  before concluding that Slack has little evidence.
- Ignore bot noise, joins/leaves, and duplicate notifications unless they are
  directly relevant.

## Read

```bash
curl -s "https://slack.com/api/conversations.history?channel=CHANNEL_ID&limit=50" \
  -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN"
```

Relevant thread:

```bash
curl -s "https://slack.com/api/conversations.replies?channel=CHANNEL_ID&ts=THREAD_TS&limit=100" \
  -H "Authorization: Bearer openshell:resolve:env:SLACK_BOT_TOKEN"
```

Common failures:

- `not_in_channel`: the bot is not a member.
- `missing_scope` with `channels:history`: cannot read public history.
- `missing_scope` with `groups:history`: cannot read private history.
- `channel_not_found`: wrong ID or unavailable to the token.

## Answer

- Answer directly in plain text.
- Back important claims with specific evidence: channel, date/time, thread, user
  name, or short message phrase when useful.
- Do not dump raw message counts, participant lists, or scope unless it affects
  confidence or the user asks.
- Do not force a fixed summary format. Use normal sentences and only the light
  structure needed for readability.

## Pitfalls

- Do not use `session_search` to discover Slack channel IDs.
- Do not start channel discovery with `users.conversations`.
- If the channel ID is already known, skip discovery and read history directly.
