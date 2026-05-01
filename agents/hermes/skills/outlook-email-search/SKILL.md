---
name: outlook-email-search
description: Search the Outlook mailbox via Microsoft Graph to find and read emails that help answer user questions.
---

# outlook-email-search

Use this skill to search emails and answer questions that require reading mail
— finding a specific message, summarizing a thread, checking whether something
was communicated, or pulling context from recent correspondence.

## When to use

- "Did I get an email about X?"
- "What did [person] say about [topic]?"
- "Summarize the emails about [project] from the last two weeks"
- "Check if [decision/approval/update] was sent to me"
- "Find unread emails from [sender]"

## Access model

- All Graph API requests go through the credential sidecar on `127.0.0.1:8766`.
- Use `Authorization: Bearer OUTLOOK_TOKEN_PLACEHOLDER` — the sidecar swaps
  this for a live delegated token before forwarding to `graph.microsoft.com`.
- The helper script reads `GRAPH_SIDECAR_URL` from the environment or from
  `/sandbox/.hermes-data/.env`, and defaults to `http://127.0.0.1:8766`.
- **Two mailbox env vars** — understand the distinction:
  - `OUTLOOK_REPLY_TO` — the **human owner's** personal address (e.g. `you@nvidia.com`).
    When the user says "my emails", this is what they mean. This is the
    primary target for search.
  - `OUTLOOK_TARGET_MAILBOX` — the **agent's** polling mailbox
    (e.g. `agt-you@nvidia.com`). The bridge monitors this for task requests.
    Only used as a fallback if `OUTLOOK_REPLY_TO` is not set.
  - The delegated token (from the agent account) has `Mail.ReadWrite.Shared`
    which grants read access to the human's mailbox via `/users/EMAIL/` in Graph.

## Procedure

### 1. Run the search helper

The script is at:
```
/sandbox/.hermes-data/skills/outlook-email-search/scripts/search_emails.py
```

```bash
python3 /sandbox/.hermes-data/skills/outlook-email-search/scripts/search_emails.py [OPTIONS]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--query TEXT` | Free-text keyword search (KQL) — searches subject, body, sender |
| `--subject TEXT` | Subject contains this text |
| `--from EMAIL` | Exact sender email address |
| `--since DATE` | Messages after date (`2026-04-01`, or relative `7d`, `2w`, `1m`) |
| `--until DATE` | Messages before date |
| `--folder NAME` | `inbox` (default), `sent`, `drafts`, `archive`, `junk` |
| `--mailbox NAME` | `auto`/`reply`/`human` for the human owner (default), `target`/`agent` for the agent polling mailbox |
| `--top N` | Max results (default 20, max 50) |
| `--unread` | Unread messages only |
| `--body` | Fetch full body text (makes one extra Graph request per message) |

At least one filter is required.

### 2. Interpret the output

The script returns JSON:
```json
{
  "ok": true,
  "count": 3,
  "messages": [
    {
      "id": "AAMk...",
      "subject": "Q1 budget approval",
      "from": "manager@nvidia.com",
      "from_name": "Jane Manager",
      "received": "2026-04-15T14:32:00Z",
      "is_read": false,
      "has_attachments": true,
      "preview": "Hi Matt, the Q1 budget has been approved..."
    }
  ]
}
```

The `preview` field is the first ~250 characters of the body. Use `--body` when
you need the full text to answer the question.

### 3. Fetch a specific message (if needed)

If the preview is not enough and `--body` would return too many results, fetch
one message directly:

```bash
# Replace USER@nvidia.com with the value of OUTLOOK_REPLY_TO
curl -s "http://127.0.0.1:8766/v1.0/users/USER@nvidia.com/messages/MESSAGE_ID?\$select=subject,body,from,receivedDateTime" \
  -H "Authorization: Bearer OUTLOOK_TOKEN_PLACEHOLDER" | python3 -c "
import json, sys, html, re
d = json.load(sys.stdin)
content = d.get('body', {}).get('content', '')
content = re.sub(r'<[^>]+>', ' ', content)
content = html.unescape(content)
print(re.sub(r'\s+', ' ', content).strip()[:5000])
"
```

### 4. Synthesize and answer

Read the results and answer the user's question directly. If no results were
returned, say so clearly rather than guessing. Suggest a broader search if the
criteria may have been too narrow.

#### Format for summary requests

When the user asks for a summary or overview of emails (not a specific lookup),
use this compact format — do not produce flowing prose:

```
**Inbox — {date}, {N} messages**

**{Category}**
- {Subject} ({Sender first name}) — {one-line takeaway}
- …

**{Category}**
- …

**Bottom line:** {2–3 sentence synthesis of the day's main themes.}
```

Rules:
- Category headers group related threads. Use 4–6 categories max; merge thin
  ones into "Other".
- Each bullet: subject (trimmed if long), sender first name only, em-dash,
  one-line takeaway. No nested bullets.
- Omit the verbose intro sentence ("Here's a summary of … based on … messages
  returned …"). The header line is enough context.
- Skip purely automated/bot messages (GitHub notifications, OTP codes, marketing
  newsletters) unless directly relevant to the user's question. Note how many
  were skipped if more than 5.
- Use "Bottom line:" not "Overall".

## Common patterns

**Find emails about a topic from this week:**
```bash
python3 .../search_emails.py --query "budget approval" --since 7d
```

**What did a specific person send recently?**
```bash
python3 .../search_emails.py --from person@nvidia.com --since 30d --top 10
```

**Unread emails with full body:**
```bash
python3 .../search_emails.py --unread --body --top 10
```

**Search sent folder for something you sent:**
```bash
python3 .../search_emails.py --query "project update" --folder sent --since 2w
```

**Check for a specific subject in a date window:**
```bash
python3 .../search_emails.py --subject "Q1 report" --since 2026-04-01 --until 2026-04-30
```

**Debug an incoming email sent to the agent mailbox:**
```bash
python3 .../search_emails.py --mailbox target --subject "Agent Labs Summary" --since 7d --body
```

## Pitfalls

- `--body` is significantly slower — it makes one Graph request per message.
  Use it only when `preview` is insufficient.
- `--query` uses KQL full-text search; `--orderby` (newest first) is dropped
  when `--query` is active (Graph API constraint). Results are still relevant
  but not date-sorted.
- Treat `--query` as plain free text, not Graph KQL. Use `--subject`,
  `--from`, `--since`, and `--until` for field-specific searches rather than
  strings like `subject:"..."` or `from:person@example.com`.
- `--subject` and `--query` can be combined. The script uses Graph filters for
  structured fields and local matching when Graph cannot combine filters with
  full-text search.
- `--from` uses OData `$filter` for an exact email match. Do not use it for
  partial name matching — use `--query "from:Name"` instead.
- Searches target the human's mailbox (`OUTLOOK_REPLY_TO`), not the agent's
  polling mailbox (`OUTLOOK_TARGET_MAILBOX`). The agent has delegated access to
  read the human's mail via `Mail.ReadWrite.Shared`.
- If a request arrives by email, the request email itself is already in the
  prompt and lives in the agent polling mailbox. Do not search the human mailbox
  for the task email's sender/subject unless the user explicitly asks for that
  message; search the user's requested topic instead.
- Do not claim Outlook is unavailable just because one search returns no results.
  Try a broader query or different date range first.
