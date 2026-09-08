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
- LLM provider/key clarification is pending; real inference cannot run until set.
- Service implemented in `service/`, Compose in `compose.service.yaml`, client in
  `test_client.py`, docs in README and `docs/VALIDATION.md`.
- API running at http://localhost:8080 (docs at /docs), database migrations applied.
- 13 PostgreSQL tests pass, including the actual client over real HTTP with fixtures.
- Real Harbor/Docker installed-agent smoke passed with local model fixture; no live
  LLM performance measurement yet. All 10 selected task IDs validated.
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
   worker is waiting without claiming jobs because OPENAI_API_KEY is absent.
2. Commit implementation and open draft PR. Do not include `.env` or
   local assignment images. Original README preserved at docs/UPSTREAM_README.md.
3. Await user LLM key/provider. Add key directly in `.env`, recreate API/worker,
   run `python test_client.py --task-ids fix-git --max-iterations 0`, then full client.
4. Record measured outcomes/runtime in docs/VALIDATION.md and update PR.

Temporary `harness-dev` container has read-only source mount and shared artifacts;
remove it when finished. Existing Supabase containers remain untouched.

Useful checks:

```sh
docker compose -f compose.service.yaml up --build -d
docker compose -f compose.service.yaml run --rm --no-deps -e DATABASE_URL=postgresql://harness:local-development-only@db:5432/harness_test api python -m pytest -q
docker compose -f compose.service.yaml run --rm --no-deps worker python -m tests.sandbox_smoke
```

The test database `harness_test` already exists. Tests refuse other database names.
Do not run an original all-89-task baseline or the optimization PROGRAM.md loop.
