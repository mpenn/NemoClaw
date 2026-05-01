---
name: nvidia-forum-search
description: Search mirrored NVIDIA Developer Forums data from the source-etls REST bridge.
---

# nvidia-forum-search

Use this skill to research NVIDIA Developer Forums data from inside the sandbox.

## When to use

- Find recent activity or announcements on a topic
- Check whether an issue has been discussed on the forums
- Gather community discussion for a specific project or feature

## Access model

**Do NOT attempt to reach forums.developer.nvidia.com or docs.nvidia.com
directly.** The sandbox has no egress to NVIDIA forum or docs hosts — those
requests will be blocked. The only path to forum data is the source-etls REST
mirror described below.

## Procedure

### 1. Run the query script via terminal

Use the terminal tool to run these commands directly — do not attempt to invoke
`source-etl-query` as a named skill tool.

```bash
python3 /sandbox/.hermes-data/skills/source-etl-query/scripts/query_source_etl.py forum-topics --limit 20
python3 /sandbox/.hermes-data/skills/source-etl-query/scripts/query_source_etl.py forum-topics --search nemoclaw --limit 10
```

### 2. Use topic IDs or titles to refine the research scope

### 3. Treat missing results as an ETL/data-scope question first

If results are empty or don't match what the user asked about, follow the
error-handling guidance in the `source-etl-query` skill to explain what forum
tag the mirror actually covers and whether the ETL has synced yet. Do not fall
back to direct forum requests.

## Pitfalls

- The ETL mirrors one configured forum tag — it does not cover the entire
  forums site. If the user asks about a topic outside that tag, explain the
  scope limit.
- The mirrored dataset may lag the live forums by up to the ETL refresh
  interval.
