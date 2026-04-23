---
name: nvidia-forum-search
description: Search the NVIDIA Developer Forums for recent posts and discussions.
---

# nvidia-forum-search

Use this skill to search the NVIDIA Developer Forums from inside the sandbox.

## When to use

- Find recent activity or announcements on a topic
- Check whether an issue has been discussed on the forums
- Gather community discussion for a specific project or feature

## Access model

- Use the JSON search endpoint.
- Keep requests light because the forum is rate-limited in this environment.

## Procedure

### 1. Run one search request

```bash
curl -s --max-time 10 \
  "https://forums.developer.nvidia.com/search.json?q=SEARCH_TERM&order=latest"
```

### 2. Parse the result

Use `python3` if needed to extract titles, dates, or topic IDs from the JSON.

### 3. Stop quickly on throttling

If the body says `"Slow down"`, if the response shows an error payload, or if
the request fails, treat the forums as unavailable for this turn and move on.

## Pitfalls

- Do not retry the same search term repeatedly.
- Do not switch to browser automation as a fallback for the same forum query.
- Rate limiting is common, so keep searches targeted.
