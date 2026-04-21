---
name: nvidia-forum-search
description: Search the NVIDIA Developer Forums for recent posts and discussions.
---

# nvidia-forum-search

Search the NVIDIA Developer Forums for recent posts and discussions.

## When to use

- Find recent activity or announcements on a topic
- Check whether a known issue has been reported
- Summarise community discussion for a specific project or feature

## Hard limits — read before starting

- **One attempt per search term.** If the request is rate-limited or returns no
  results, record "no data available" and move on. Do not retry, sleep, or vary
  the URL to try again.
- **No sleeping.** Never use `sleep` to wait out a rate limit. If the forum is
  throttling you, skip it entirely and note it in your response.
- **Two searches maximum per task.** If you need results for multiple terms,
  pick the two most important ones and skip the rest.
- **Fail fast.** A single rate-limit response (`"Slow down"` in the body, or
  HTTP 429) means the forum is unavailable for this session. Stop immediately.

## Procedure

### 1. Try the JSON search endpoint (one request)

```bash
curl -s --max-time 10 \
  "https://forums.developer.nvidia.com/search.json?term=SEARCH_TERM&order=latest" \
  -o /tmp/forum_results.json
```

Check the exit code and the response:

```bash
# success path
python3 -c "
import json, sys
try:
    d = json.load(open('/tmp/forum_results.json'))
    topics = d.get('topics', {}).get('topics', [])
    for t in topics[:5]:
        print(t.get('created_at','')[:10], t.get('title',''))
except Exception as e:
    print('parse error:', e)
"
```

### 2. If rate-limited or empty, stop

If the response body contains `"Slow down"`, `error_type`, or HTTP 429, or if
`curl` exits non-zero, write: *"NVIDIA Developer Forums unavailable (rate
limited) — skipping."* and continue with the rest of the task.

Do **not** try `browser_navigate` as a fallback for the same URL. The browser
tool goes through the same proxy and will receive the same throttle.

## Pitfalls

- The forum uses Cloudflare rate limiting that applies per-IP across all
  request methods. Spacing out requests with `sleep` does not reliably help and
  wastes significant time when the timeout exceeds the 10-minute budget.
- `browser_navigate` to `forums.developer.nvidia.com` consistently fails in
  this environment — skip it.
- The HTML endpoint (`/search?q=…`) and the JSON endpoint (`/search.json?term=…`)
  share the same rate-limit quota. Trying both after a 429 doubles the penalty.
- `/latest.json` is also rate-limited — do not use it as a fallback.
