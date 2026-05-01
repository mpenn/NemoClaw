---
name: slack-channel-finder
description: Discover Slack channels by topic, team, or domain and infer what each channel is for. Use when the user wants to find which channels are relevant to a topic ("which channels does the inference team use", "where do we discuss deployments") or to understand what an unfamiliar channel is for. Pairs with slack-channel-summarizer for follow-up reads.
---

# slack-channel-finder

Use this skill to discover Slack channels matching a topic, team, or domain,
and to infer what each channel is for. The skill is restricted to channels
the bot has been added to.

## When to use

- "Which channels does the X team use?"
- "Find me Slack channels about Y"
- "Where do we discuss Z?"
- "What is #cryptic-channel-name for?"
- The user wants to discover channels they don't already know by name
- The user wants to understand an unfamiliar channel before reading history

Do NOT use this skill when the user has already named a specific channel and
just wants its history summarized — use slack-channel-summarizer instead.

## Access model

- The bot token is available as `openshell:resolve:env:SLACK_BOT_TOKEN`.
- The bot can only see channels it has been invited to (private) or that are
  public and visible to the token.
- Required OAuth scopes: `channels:read` for public channels, `groups:read` for
  private channels. The lister defaults to public-only; pass
  `--types public_channel,private_channel` only if `groups:read` is present.
- All inference is based on signals the bot can actually retrieve. Never
  fabricate channel IDs, names, or characteristics.

## Procedure

### 1. List candidate channels

Use the bundled lister script to get all channels the bot is a member of:

```bash
python3 /sandbox/.hermes-data/skills/slack-channel-finder/scripts/list_accessible_channels.py
```

This returns a JSON array of `{id, name, topic, purpose, is_archived, num_members}`.

Interpret the result:

- `ok: true` with a non-empty `channels` list — proceed.
- `missing_scope` — the script was called with `--types public_channel,private_channel`
  but the bot token lacks `groups:read`. Retry without private channels:
  ```bash
  python3 .../list_accessible_channels.py --types public_channel
  ```
  If public channels also fail, the token itself is broken — tell the user.
  Do NOT say "Slack is unavailable." Do NOT tell the user to invite the bot
  to channels — that is unrelated to scope errors.
- Empty list — the bot has not been added to any channels. Tell the user.

### 2. Match against the user's query

For topic queries, score each channel by:

1. **Channel name token match** (highest weight) — split the name on `-`, `_`,
   `.` and `/`, expand common abbreviations (eng → engineering, ml → machine
   learning, k8s → kubernetes, etc.), then match against query tokens.
2. **Topic field match** — substring match against `topic.value`, ignoring
   stale topics (older than ~1 year via `topic.last_set`).
3. **Purpose field match** — same substring approach as topic.

For team queries ("the inference team's channels"), include channels whose
name contains the team identifier, plus channels where matching is ambiguous
but where a `describe_slack_channel.py` lookup confirms team relevance.

If the query is semantically loose ("incidents" vs "outages",
"deployments" vs "releases"), return matches for related terms but flag in
the response that matching is literal — encourage the user to add synonyms
if results look thin.

### 3. Return ranked candidates

For the top 3-5 channels, return:

- channel reference as `<#CHANNEL_ID|channel-name>` so it renders as a link
- a one-line reason it matched (which signal triggered)
- archive/membership flags if relevant

If nothing matches, say so plainly: "I don't see a matching channel, but I
can only see channels I've been added to. Try inviting the bot to more
channels, or share a channel mention directly."

### 4. Optionally describe candidates

If the user wants to understand *what* a channel is for (not just confirm
it matched), call the describer script for each candidate:

```bash
python3 /sandbox/.hermes-data/skills/slack-channel-finder/scripts/describe_slack_channel.py --channel-id CHANNEL_ID
```

For fast multi-channel scans, pass `--no-history` to skip the
`conversations.history` call and rely only on name, topic, purpose, pins,
and bookmarks. Reserve full mode for the top 1-3 candidates.

The script returns structured `signals` — it does NOT produce a natural-
language description. Synthesize the description yourself from the signals,
weighting them in this order:

1. Pinned messages (often a charter or intro)
2. Channel name tokens
3. Topic and purpose (if not stale)
4. Bookmarks
5. Recent human message themes
6. Top contributors

The `confidence` field (`high`, `medium`, `low`) reflects how many independent
signals were available. For `low`-confidence channels, hedge the description
("appears to be about ...") or ask the user to confirm.

### 5. Chain into summarization if requested

If the user's goal goes beyond discovery ("tell me what the X team is working
on"), once channels are identified, hand off to `slack-channel-summarizer`
for each top channel. Cap at 5 channels per query to keep cost and latency
reasonable; surface the ranking so the user can ask for more.

## Pitfalls

- Do not claim a channel doesn't exist when the bot simply hasn't been
  invited. Always say what is actually true: "I don't see one in the
  channels I have access to."
- Do not rely solely on `topic` and `purpose` — many teams leave them empty
  or stale. Channel name and pinned messages are usually stronger signals.
- Do not return archived channels unless the user explicitly asks for them.
- Do not invent channel IDs, names, or descriptions. Only return what the
  API actually returned.
- Do not run `describe_slack_channel.py` in full mode across dozens of
  channels — use `--no-history` for breadth, full mode for the final 1-3.
- Do not characterize a channel as inactive based on a single
  `conversations.history` window — low-volume channels may be quarterly or
  incident-only.
- Bot-generated messages (GitHub, Jira, CI integrations) dominate volume in
  most engineering channels and carry no human topic signal. The describer
  script filters these automatically; do not undo that filtering when
  judging "what people actually talk about."
