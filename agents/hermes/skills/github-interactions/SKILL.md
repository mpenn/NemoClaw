---
name: github-interactions
description: Access GitHub repos, issues, PRs, and repo activity from inside the NemoClaw sandbox.
---

# github-interactions

Use this skill for GitHub access from inside the sandbox.

## When to use

- Fetch issues, PRs, or repo activity
- Inspect repository metadata
- Clone or work with a repository

## Access model

- Use `gh` for GitHub API access.
- Use `git` for repository operations.
- Do not use `curl` for GitHub API calls in this sandbox.

## Procedure

### 1. Use `gh` for API reads

```bash
gh issue list --repo OWNER/REPO --limit 20 --json number,title,createdAt,state
gh issue view NUMBER --repo OWNER/REPO --json title,body,comments
gh pr list --repo OWNER/REPO --limit 20 --json number,title,createdAt,state
gh api repos/OWNER/REPO/issues --paginate
```

### 2. Use `git` for repository operations

```bash
git clone https://github.com/OWNER/REPO.git
```

### 3. Filter or post-process if needed

If you need more control, use `python3` around `gh api` output rather than
switching to `curl`.

## Pitfalls

- `curl` to GitHub will be blocked by policy even with valid headers.
- `gh` already has the right authentication path. Do not look up or inject the
  token manually.
