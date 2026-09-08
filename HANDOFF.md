# Agent optimization service handoff

## Objective and reference

Complete all five take-home milestones today: HTTP API, durable asynchronous
processing, sandboxed TerminalBench execution, automatic LLM optimization with
persisted history, and organization roles. Deliver README, test_client.py, branch
and PR. Assignment images: `Question page 1.png`, `Question page 2.png`.

## Current state

- Branch: `feat/agent-optimization-service`.
- Original repo inspected. TerminalBench runner and agent template exist; the
  original loop is instructions for an external coding agent, not a service.
- Docker Desktop is running Linux containers. Existing Supabase containers belong
  to another project; leave them alone.
- E2B credential saved in ignored `.env`. Never print or commit credential values.
- OpenAI key supplied by user and saved in ignored `.env`; worker recreated.
- Live single-task baseline completed: job `4b0a2329-8f05-495d-ace8-07e4d9a272ab`,
  organization `036172db-b1cd-43d9-b81e-8b248a97b850`. Client output goes to
  ignored `workspace/live-baseline.json`; local client credentials in the matching
  `workspace/client-<org-id>.json`. Never print these credentials.
- Single task: service succeeded, task failed (reward 0); agent resolved one Git
  conflict incorrectly. Actual model usage: 13,835 input / 536 output tokens.
- Full 10-task optimization job completed: `efbafbbc-8ceb-4607-9cf6-d75bb29cdd73`, org
  `bae13d3c-d367-46ca-baf9-3f7b61e1f7c6`, output `workspace/live-optimization.json`.
  Duration 16m47s; baseline 1/10, candidate 4/10. Both used gpt-4.1-mini and had zero
  runner errors. Candidate rejected because nginx-request-logging regressed, so
  best_score remains 0.1 and stop_reason is no_improvement. One automatic proposal
  strengthened command-exit-status checks/error recovery. No manual prompt edits.
  Full results retrieved through client; sanitized summary: docs/live-results.json.
- Service implemented in `service/`, Compose in `compose.service.yaml`, client in
  `test_client.py`, docs in README and `docs/VALIDATION.md`.
- API running at http://localhost:8080 (docs at /docs), database migrations applied.
- 13 PostgreSQL tests pass, including the actual client over real HTTP with fixtures.
- Real Harbor/Docker installed-agent smoke passed with local model fixture, and
  live OpenAI baseline/optimization completed. All 10 selected task IDs validated.
- E2B is saved for future use; the service currently implements Docker only.

## Implementation direction

- FastAPI + PostgreSQL, separate worker, PostgreSQL queue with leases and fencing.
- Separate Compose project; API has no Docker socket. Worker controls Harbor.
- Fixed Harbor adapter installs/runs agent Python inside each task sandbox.
- Docker task environments with dedicated Docker-in-Docker engine. Worker connects
  using mutual TLS. API has no engine access. E2B adapter is deferred.
- Optimize validated system prompts; persist full runnable source, proposal,
  results and acceptance decisions, including rejected iterations.
- Strict improvement acceptance, stop on plateau/iteration cap; preserve best.
- Tenant membership/role checks for every job/history route, admin membership
  management, members see only their own jobs.
- Tests use real PostgreSQL plus explicit deterministic fake runner/optimizer;
  real benchmark execution is validated separately and reported honestly.

## Environment

Host PowerShell, Python 3.13; uv and gh not found. Docker works outside sandbox.
Git requires `git -c safe.directory=C:/Users/Calvin/dev/auto-harness ...` because
the tool sandbox user differs from the repository owner. Git writes require
escalation. Read `.env` only to check key presence without showing values.

## Next

1. Final locked-image rebuild completed; 13 tests pass and final sandbox smoke
   (including unauthenticated Docker API denial) passes. API and worker are running;
   worker has now been recreated with the user-provided OpenAI key.
2. Implementation committed as `c086b76`. Git push failed: local GitHub credential
   rejected. Connected GitHub app login is `calvinxiang`; upstream metadata reports
   push=false. `calvinxiang/auto-harness` returned 404. Publishing needs a writable
   fork/access and usable authentication. Exact PR draft is docs/PR_DRAFT.md. No PR
   was opened. Do not include `.env` or local assignment images.
3. Live single-task baseline and full client run are complete; results, runtime and
   token usage recorded in docs/VALIDATION.md. README and PR draft updated.
4. Remaining delivery step is publishing the branch and opening the PR once GitHub
   access is available. No further model runs are needed for integration validation.

Temporary `harness-dev` container was removed. No benchmark task containers remain.
Existing
Supabase containers remain untouched. Original README: docs/UPSTREAM_README.md.

Useful checks:

```sh
docker compose -f compose.service.yaml up --build -d
docker compose -f compose.service.yaml run --rm --no-deps -e DATABASE_URL=postgresql://harness:local-development-only@db:5432/harness_test api python -m pytest -q
docker compose -f compose.service.yaml run --rm --no-deps worker python -m tests.sandbox_smoke
```

The test database `harness_test` already exists. Tests refuse other database names.
Do not run an original all-89-task baseline or the optimization PROGRAM.md loop.
