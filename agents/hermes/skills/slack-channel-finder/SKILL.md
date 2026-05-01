---
name: slack-channel-finder
description: Discover accessible Slack channels that may contain evidence for a topic, team, or domain.
---

# slack-channel-finder

Use this skill when the user asks where something is discussed, names a topic
without a channel, or asks to understand what an unfamiliar Slack channel is
for. Pair it with `slack-channel-summarizer` when the goal is research, not just
channel discovery.

## Access

- Token: `openshell:resolve:env:SLACK_BOT_TOKEN`.
- The bot can see public channels visible to the token and private channels it
  has joined.
- Scripts:
  - `/sandbox/.hermes-data/skills/slack-channel-finder/scripts/list_accessible_channels.py`
  - `/sandbox/.hermes-data/skills/slack-channel-finder/scripts/describe_slack_channel.py`

## Find Candidates

```bash
python3 /sandbox/.hermes-data/skills/slack-channel-finder/scripts/list_accessible_channels.py
```

If private channel discovery lacks scope, retry public-only. Do not claim Slack
is unavailable unless public lookup also fails.

Match broadly across channel name, topic, purpose, and likely synonyms. Include
plausible candidates when the wording is loose; the next step can read history
and discard weak matches.

For a promising or ambiguous channel, inspect signals:

```bash
python3 /sandbox/.hermes-data/skills/slack-channel-finder/scripts/describe_slack_channel.py --channel-id CHANNEL_ID
```

Use `--no-history` for broad scans. Use full mode only for the few channels most
likely to matter.

## Answer Or Continue

- For pure discovery, answer in plain text. If several channels match, a short
  one-level list with channel references and the specific matching signal is OK.
- For research, hand the best candidates to `slack-channel-summarizer` and let
  evidence from messages drive the final answer.
- If nothing matches, say what access was available. Do not imply inaccessible
  channels do not exist.

## Pitfalls

- Do not invent channel IDs or descriptions.
- Do not rely only on topic/purpose; they are often empty or stale.
- Do not include archived channels unless asked.
- Bot-heavy channels may look active but contain little human evidence.
