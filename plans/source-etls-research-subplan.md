# Source ETLs Research Subplan

Last updated: 2026-04-29

## Goal

Build a minimal host-side ETL system under `/home/slopp/NemoClaw/source-etls`
that:

- runs `docker compose up`
- starts 3 services:
  - `postgres`
  - `github-etl`
  - `forums-etl`
- syncs hourly
- backfills the last 72 hours on first run
- stores raw and normalized GitHub/forum data in Postgres
- supports delta syncs and idempotent re-runs

## Chosen Direction

Use a mixed approach:

- GitHub ETL:
  `MeltanoLabs/tap-github` -> `target-postgres`
- Forums ETL:
  thin custom Python ETL using `dlt` + `httpx`
- Shared sink:
  Postgres

This is the minimum reusable architecture with the least custom code.

## Why This Direction

### GitHub

`tap-github` already does the hard part:

- issues
- issue comments
- issue events
- pull requests
- pull request reviews
- pull request review comments
- discussions
- discussion comments
- discussion comment replies

It also has an incremental/state model already, which is the main thing we need
for hourly delta syncs.

### NVIDIA Forums

The NVIDIA forums are Discourse-backed. That makes HTML scraping unnecessary for
the default path.

Preferred endpoint shapes:

- tag feed:
  `https://forums.developer.nvidia.com/tag/nemoclaw.json`
- topic detail:
  `https://forums.developer.nvidia.com/t/<slug>/<topic_id>.json`

The forums side is therefore better treated as a small structured ingest job
than as a browser scraper.

### Postgres

A shared Postgres sink keeps the architecture simple:

- one destination for both ETLs
- one place for Hermes to query
- one place for ETL state/checkpoint tables if needed

## OSS Candidates

### Recommended

#### `MeltanoLabs/tap-github`

Use for GitHub extraction.

Pros:

- existing OSS connector
- supports discussions and GraphQL-backed data
- supports incremental state
- avoids hand-rolling GitHub pagination and schema logic

Cons:

- Singer/Meltano state and config are less ergonomic than a pure Python
  codebase

#### `MeltanoLabs/target-postgres`

Use as the GitHub sink if we keep GitHub on Singer.

Pros:

- compatible with `tap-github`
- avoids writing a custom GitHub loader

Cons:

- schema ergonomics are shaped by Singer conventions

#### `dlt`

Use for the forums ETL.

Pros:

- strong incremental support
- easy upsert/merge loading into Postgres
- easy state handling
- good fit for a small custom API extractor

Cons:

- still requires custom forum-source code

#### `httpx`

Use for forum HTTP access.

Pros:

- thin and reliable
- enough for Discourse JSON endpoints

Cons:

- no domain-specific helpers

### Secondary / Optional

#### `PyGithub`

Only use if we later decide `tap-github` is insufficient.

Pros:

- Python-native
- good for ad hoc GitHub REST access

Cons:

- would likely push us toward re-implementing logic already covered by
  `tap-github`
- Discussions support is not the primary reason to choose it

#### Discourse client libraries such as `pydiscourse`

Optional only.

Pros:

- some convenience around Discourse APIs

Cons:

- likely unnecessary for the narrow public JSON endpoints we need
- adds abstraction where raw JSON requests are already straightforward

## Architecture

### Service Layout

#### `postgres`

Responsibilities:

- persistent storage for raw and normalized data
- optional ETL metadata/checkpoint tables

Expected volumes are small enough that one local Postgres instance is fine.

#### `github-etl`

Responsibilities:

- run hourly
- extract repo activity for a configured target repo
- write to Postgres
- use GitHub token when present
- fall back to public API when token is absent

Default repo:

- `NVIDIA/NemoClaw`

Override:

- repo owner/name via env var or compose override

#### `forums-etl`

Responsibilities:

- run hourly
- fetch topics for the `nemoclaw` tag
- hydrate changed topics into full topic/post payloads
- write to Postgres

Default topic scope:

- tag: `nemoclaw`

Override:

- tag via env var

## Incremental Strategy

### Initial Backfill

On first run, both ETLs should read from:

- `now() - 72 hours`

This is a backfill window, not a one-time full historical import.

### Ongoing Delta Loads

Use overlap windows, not strict one-way watermarks.

Recommended rule:

- every hourly run re-reads the last 72 hours of source updates
- destination tables upsert by immutable source IDs

Reason:

- GitHub and Discourse both allow edits/comments/late updates
- overlap windows reduce missed updates caused by API ordering quirks

### Keys and Cursors

GitHub immutable keys:

- issue id
- pull request id
- discussion id
- comment/review/reply id

GitHub cursors:

- `updated_at`
- stream-specific timestamps where exposed

Forum immutable keys:

- topic id
- post id

Forum cursors:

- topic `last_posted_at`
- topic/post update timestamps from topic JSON

### Storage Pattern

For each source family:

- raw table with full JSON payload
- normalized tables for common query patterns

Example normalized families:

- `github_issues`
- `github_pull_requests`
- `github_discussions`
- `github_comments`
- `forum_topics`
- `forum_posts`

## Scheduling

Target behavior:

- each ETL container self-schedules hourly

Minimal implementation options:

- container entrypoint loop with `sleep`
- cron inside container

Preferred first implementation:

- simple long-running loop in each ETL container

Reason:

- fewer moving parts than system cron inside the container
- easier logging and failure behavior

## Config Surface

### Shared

- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`

### GitHub

- `GITHUB_REPO`
  default: `NVIDIA/NemoClaw`
- `GITHUB_TOKEN`
  optional

### Forums

- `FORUM_TAG`
  default: `nemoclaw`

## Implementation Sequence

1. Create `source-etls/` skeleton.
2. Add compose file with Postgres and stub ETL services.
3. Stand up GitHub ETL with `tap-github` + `target-postgres`.
4. Stand up forum ETL with `dlt` + `httpx`.
5. Add raw + normalized table conventions.
6. Add hourly scheduling loops.
7. Add first-run backfill behavior.
8. Add ETL smoke validation and docs.

## Known Tradeoffs

### All-Meltano

Pros:

- one framework

Cons:

- no obvious high-confidence Discourse tap to reuse for this exact source

### All-dlt

Pros:

- one Python-native stack

Cons:

- would require hand-rolling much more GitHub extraction logic

### Airbyte

Pros:

- broad connector ecosystem

Cons:

- too heavy for 2 hourly jobs and one Postgres sink

## References

- GitHub Discussions GraphQL guide:
  https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions
- GitHub pull requests REST docs:
  https://docs.github.com/en/rest/pulls/pulls?apiVersion=latest
- GitHub issues REST docs:
  https://docs.github.com/rest/issues/issues?apiVersion=2022-11-28
- `tap-github`:
  https://hub.meltano.com/extractors/tap-github/
- `target-postgres`:
  https://hub.meltano.com/loaders/target-postgres/
- `dlt` incremental loading:
  https://dlthub.com/docs/general-usage/incremental-loading
- `dlt` state:
  https://dlthub.com/docs/general-usage/state
- Discourse JSON behavior:
  https://meta.discourse.org/t/add-json-for-tags/29485
- NVIDIA forums `nemoclaw` tag:
  https://forums.developer.nvidia.com/tag/nemoclaw/1196
