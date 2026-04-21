---
name: github-interactions
description: How to interact with GitHub from inside the NemoClaw sandbox — which tools are policy-approved and why curl is blocked.
---

# github-interactions

Interact with GitHub repos, issues, and PRs from inside the NemoClaw sandbox.

## When to use

- Fetch GitHub issues, PRs, or repo activity
- Clone or push to a repository
- Any task requiring GitHub API or git access

## Why curl is blocked for GitHub

The sandbox network policy restricts each external host to a specific set of
approved binaries. For GitHub, only `gh`, `git`, `python3.11`, and `hermes`
are allowed — not `curl`. This is intentional: `curl` is a general-purpose
tool that provides no authentication audit trail, whereas `gh` uses a
scoped token tied to the sandbox provider. If you attempt `curl
https://api.github.com/...` it will return 403 from the proxy even if you
supply a valid token.

## Procedure

### Fetch issues or API data — use `gh`

`gh` is pre-authenticated via the sandbox token provider. No token handling
needed:

```bash
# List recent open issues
gh issue list --repo OWNER/REPO --limit 20 --json number,title,createdAt,state

# Fetch a specific issue
gh issue view NUMBER --repo OWNER/REPO --json title,body,comments

# List recent PRs
gh pr list --repo OWNER/REPO --limit 20 --json number,title,createdAt,state
```

### Fetch API data with Python

If you need more control (pagination, filtering), use `python3`:

```python
import subprocess, json

result = subprocess.run(
    ["gh", "api", "repos/OWNER/REPO/issues", "--paginate",
     "-q", ".[].title"],
    capture_output=True, text=True
)
print(result.stdout)
```

### Clone or work with a repo — use `git`

```bash
git clone https://github.com/OWNER/REPO.git
```

## Pitfalls

- **Never use `curl` for GitHub** — it is blocked by policy regardless of
  which token or header you supply. Use `gh api` instead.
- `gh` reads the token from the sandbox environment automatically; do not
  try to pass it manually or look it up.
- `browser_navigate` to GitHub HTML pages will succeed at the proxy level
  but GitHub returns minimal content for scraping — use the API via `gh`.
