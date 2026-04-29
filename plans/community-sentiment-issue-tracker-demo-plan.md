# Community Sentiment Issue Tracker Demo Plan

Last updated: 2026-04-29

## Scope

This plan is the source of truth for the `community-sentiment-issue-tracker-demo`
work in this fork.

The scope is intentionally narrow:

- Change only the NVIDIA internal Hermes setup path, centered on
  [NVIDIA-SETUP.md](/home/slopp/NemoClaw/NVIDIA-SETUP.md) and
  [env.template](/home/slopp/NemoClaw/env.template).
- Build a new host-side `source-etls/` stack under this repo.
- Give the Hermes sandbox read-only access to a Postgres database populated by
  the ETLs.
- Update Hermes skills so GitHub/forum research uses the scraped database
  instead of live GitHub/forum egress.

Out of scope for this phase:

- Broad NemoClaw docs cleanup
- Generic tier redesign
- In-sandbox ETL execution
- Live GitHub/forum access for Hermes after the new path is in place

## Confirmed Decisions

- The target sandbox policy set for the NVIDIA setup path is:
  `slack,outlook,postgres`
- The ETL stack runs on the host, outside OpenShell.
- GitHub ETL must work for public repos without auth, but should use a GitHub
  PAT when available in `.env`.
- The default GitHub repo target is `NVIDIA/NemoClaw`.
- The default NVIDIA forum target is the `nemoclaw` tag.
- We should preserve a commit trail as we go and plan for a PR from a `slopp`
  fork into:
  `https://github.com/mpenn/NemoClaw/tree/community-sentiment-issue-tracker-demo`

## Current State

- `postgres` is not implemented as a policy preset today.
- The current NVIDIA setup path still recommends:
  `NEMOCLAW_POLICY_PRESETS=slack,github,outlook,nvidia-forum`
- Hermes repo-local skills still assume live GitHub and live NVIDIA forum
  access:
  - `agents/hermes/skills/github-interactions`
  - `agents/hermes/skills/nvidia-forum-search`
  - `agents/hermes/skills/cross-source-gap-analysis`
- There are existing uncommitted repo changes in Hermes/runtime files. Avoid
  mixing this work into those edits unless required.

## Delivery Plan

### Phase 0: Planning Artifacts

- Add this master plan file.
- Add a separate ETL research subplan file with OSS recommendations and the
  minimum viable architecture.

### Phase 1: NVIDIA Setup Path Lockdown

- Update [NVIDIA-SETUP.md](/home/slopp/NemoClaw/NVIDIA-SETUP.md) to describe
  the new host-side ETL architecture and the new preset set.
- Update [env.template](/home/slopp/NemoClaw/env.template) so the recommended
  NVIDIA path uses:
  - `NEMOCLAW_POLICY_PRESETS=slack,outlook,postgres`
  - optional GitHub token for host ETL use, not Hermes live egress
  - Postgres connection variables needed by Hermes and/or host ETLs
- Add operator guidance that GitHub/forum data is expected to flow through
  Postgres, not through live Hermes outbound access.

### Phase 2: Policy Changes

- Add a new preset file:
  `nemoclaw-blueprint/policies/presets/postgres.yaml`
- Design it as a narrow host-side DB access preset for Hermes, preferably:
  - one host name only
  - one port only
  - one binary allowlist limited to the DB query path Hermes will use
- Keep `postgres` out of broad default tiers unless later required.
- Validate that the NVIDIA path can select only:
  `slack,outlook,postgres`

Open design point to resolve during implementation:

- whether Postgres should be reached as:
  - a fixed hostname such as `host.openshell.internal`
  - a dedicated DNS name
  - another OpenShell-supported host bridge path

### Phase 3: Host-Side `source-etls/`

- Create a new top-level `source-etls/` directory.
- Add a compose stack with 3 services:
  - `postgres`
  - `github-etl`
  - `forums-etl`
- Schedule both ETLs hourly.
- Implement first-run 72 hour backfill.
- Persist ETL state/checkpoints.
- Load raw payloads plus normalized tables into Postgres.

See the ETL subplan for the implementation shape.

### Phase 4: Hermes DB Access

- Add the skill and runtime path needed for Hermes to query the Postgres DB.
- Keep DB access read-only from the sandbox.
- Decide on the thinnest practical query path:
  - direct `psql` access, or
  - a very small helper script/tool with constrained SQL patterns

Default preference:

- use a narrow helper rather than exposing unrestricted SQL authoring to the
  model

### Phase 5: Hermes Skill Updates

- Update `github-interactions` to describe Postgres-first access for historical
  repo activity.
- Update `nvidia-forum-search` to describe Postgres-first access for forum
  research.
- Update `cross-source-gap-analysis` to treat Postgres as the canonical
  synthesized source for GitHub/forum data, while Slack remains live.
- Add a new Hermes skill if needed for querying the ETL database directly.

### Phase 6: Validation

- Add tests for policy preset discovery and application of `postgres`.
- Add tests for the NVIDIA setup path defaults if needed.
- Add ETL smoke validation:
  - containers start
  - first sync completes
  - subsequent sync is incremental
- Add Hermes-side validation that the DB query path works inside the sandbox.

## Risks

- Existing dirty changes overlap likely edit areas, especially:
  - `src/lib/onboard.ts`
  - `agents/hermes/start.sh`
  - `nemoclaw-blueprint/policies/presets/outlook.yaml`
- The current uncommitted Outlook preset change broadens access with
  `access: full`; validate whether that is intentional before relying on it.
- GitHub Discussions extraction is materially different from issues/PRs and
  should not be hand-rolled unless the chosen OSS path fails.
- NVIDIA forums are Discourse-backed but public JSON behavior can be quirky; use
  overlap windows and idempotent upserts rather than assuming perfect
  watermarks.

## Planned Commit Structure

1. Planning artifacts only
2. Policy preset + NVIDIA setup path changes
3. `source-etls/` scaffold and working compose stack
4. Hermes DB access path + skill changes
5. Tests and cleanup
