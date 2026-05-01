---
name: outlook-email-search
description: Search the human owner's Outlook mailbox via Microsoft Graph to answer questions from email evidence.
---

# outlook-email-search

Use this skill when the answer depends on Outlook mail: finding messages,
understanding a thread, checking whether something happened, or researching a
topic from recent correspondence.

## Access

- Graph requests go through the sidecar at `GRAPH_SIDECAR_URL`, usually
  `http://127.0.0.1:8766`.
- Use `Authorization: Bearer OUTLOOK_TOKEN_PLACEHOLDER`; the sidecar replaces it
  with a live delegated token.
- Search the human mailbox by default: `OUTLOOK_REPLY_TO`.
- Use the agent mailbox, `OUTLOOK_TARGET_MAILBOX`, only when debugging task
  delivery to the agent.
- Helper script:
  `/sandbox/.hermes-data/skills/outlook-email-search/scripts/search_emails.py`

## Research Approach

- Understand the information need, not just the email that delivered the task.
  Do not search for the request email's sender or subject unless the user asks
  for that message.
- Search broadly first, then narrow. Combine likely keywords, aliases, people,
  recipients, and time windows. For a mailing list, group, or DL, search by
  recipient display name/address.
- Start with previews across a broad candidate set. Fetch bodies only for the
  messages that look relevant.
- If the first pass is thin, stale, or dominated by bot/agent replies, broaden
  the terms, recipients, folders, or date range once or twice before answering.
- Treat automated agent replies and quoted old failures as non-evidence unless
  the user is asking about the bridge itself.

## Search

```bash
python3 /sandbox/.hermes-data/skills/outlook-email-search/scripts/search_emails.py [OPTIONS]
```

Useful options:

| Flag | Use |
|------|-----|
| `--query TEXT` | broad text search over subject/body/sender |
| `--subject TEXT` | subject contains text |
| `--from EMAIL` | exact sender address |
| `--to EMAIL_OR_NAME` | To-recipient match |
| `--cc EMAIL_OR_NAME` | Cc-recipient match |
| `--recipient EMAIL_OR_NAME` | To or Cc recipient match |
| `--since DATE` / `--until DATE` | absolute or relative windows like `7d`, `2w`, `1m` |
| `--folder NAME` | `inbox`, `sent`, `drafts`, `archive`, `junk` |
| `--mailbox NAME` | `auto`/`human`/`reply` or `agent`/`target` |
| `--top N` | results to return, max 50 |
| `--scan N` | recent messages to scan for local filters |
| `--body` | fetch full body text for each result |

Examples:

```bash
python3 .../search_emails.py --query "project alias decision" --since 30d --top 30
python3 .../search_emails.py --recipient "team list" --since 7d --scan 500 --top 30
python3 .../search_emails.py --recipient team-list@example.com --since 7d --body --top 20 --scan 500
python3 .../search_emails.py --mailbox target --subject "request subject" --since 7d --body
```

## Answer

- Answer the user's question directly in plain text.
- Back important claims with specific evidence: sender, date, subject, list,
  thread, or short quoted phrase when useful.
- Do not dump search logs, raw result counts, or mailbox scope unless it affects
  confidence or the user asks.
- Do not force a fixed format such as top-five, categories, or action items. Use
  normal sentences and only the light structure needed for readability.
- If evidence is weak or missing, say what was checked and what limits the
  conclusion.

## Pitfalls

- `--query` is plain free text. Use structured flags for subject, sender,
  recipient, folder, and dates.
- Graph does not reliably server-filter recipient collections here; recipient
  flags use local filtering. Increase `--scan` for busy mailboxes.
- `--body` is slower because it fetches each message body separately.
- Search the human mailbox for research. Search the agent mailbox only for task
  delivery/debugging.
- Do not claim Outlook is unavailable from old automated replies. Run a current
  helper query and report the actual error if it fails.
