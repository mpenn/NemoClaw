---
name: github-interactions
description: Access GitHub repo activity from the source-etls REST mirror inside the NemoClaw sandbox.
---

# github-interactions

Use this skill for GitHub repo research from inside the sandbox.

## When to use

- Fetch issues, PRs, or repo activity
- Inspect repository metadata
- Review mirrored GitHub discussions without requiring live GitHub egress

## Access model

**Do NOT use `gh` CLI or any direct GitHub API calls.** The sandbox has no
egress to github.com — those requests will be blocked. The only path to GitHub
data is the source-etls REST mirror described below.

## Procedure

### 1. Run the query script via terminal

Use the terminal tool to run these commands directly — do not attempt to invoke
`source-etl-query` as a named skill tool.

```bash
python3 /sandbox/.hermes-data/skills/source-etl-query/scripts/query_source_etl.py github-issues --limit 20
python3 /sandbox/.hermes-data/skills/source-etl-query/scripts/query_source_etl.py github-prs --limit 20
python3 /sandbox/.hermes-data/skills/source-etl-query/scripts/query_source_etl.py github-discussions --limit 20
```

### 2. Filter by text when needed

```bash
python3 /sandbox/.hermes-data/skills/source-etl-query/scripts/query_source_etl.py github-issues --search sandbox --limit 10
python3 /sandbox/.hermes-data/skills/source-etl-query/scripts/query_source_etl.py github-prs --search outlook --limit 10
```

### 3. Treat missing data as an ETL scope issue

If results are empty or don't match what the user asked about, follow the
error-handling guidance in the `source-etl-query` skill to explain what repo
the mirror actually contains and whether the ETL has synced yet. Do not fall
back to live GitHub requests.

## Pitfalls

- The mirrored dataset may lag the source by up to the ETL refresh interval.
- The ETL targets one configured repo — confirm that scope matches the user's
  request before assuming a record is missing.
