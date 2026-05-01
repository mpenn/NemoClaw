---
name: cross-source-gap-analysis
description: Research a question across multiple configured sources and synthesize what the evidence supports.
---

# cross-source-gap-analysis

Use this skill when one source is unlikely to be enough: Slack plus email,
GitHub plus forums, several channels, or any request for a broader view.

## Approach

- Start source-neutral. Ask what evidence would answer the user's question, then
  choose the relevant source skills.
- Search broadly enough to avoid anchoring on the first hit or one source type.
  Use multiple terms, aliases, likely channels/lists/repos, and reasonable time
  windows.
- Refine after the broad pass. Keep the strongest, most relevant evidence; drop
  duplicates, bot noise, and low-signal hits.
- Compare sources only when comparison helps. Do not force a gap-analysis frame
  onto a plain research or summary request.
- Distinguish absence of evidence from evidence of absence. Say when a source
  was sampled, inaccessible, or too thin to support a strong claim.

## Answer

- Lead with the answer.
- Keep it concise and write in plain text.
- Support important claims with specific evidence from the checked sources:
  message, email subject, issue, PR, forum topic, date, author, or channel.
- Include search scope only when it explains confidence, limits, or surprising
  results.

## Pitfalls

- Do not over-collect after the answer is already clear.
- Do not hide conflicting evidence; summarize the disagreement briefly.
- Do not treat mirrored data as proof of live-source absence.
