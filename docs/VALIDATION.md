# Validation record

Recorded 2026-09-08 on Windows Docker Desktop using Linux containers.

- 13 PostgreSQL integration/edge tests passed. One non-failing upstream Starlette/
  AnyIO deprecation warning appeared.
- `test_client.py` submitted to a live HTTP server, polled and printed full
  two-iteration history. This test used deterministic runner/optimizer fixtures.
- Real Harbor 0.1.45 + Docker task + installed runtime + verifier smoke passed.
  Latest run ID: `a2f2dc97-31b4-474e-a34f-821a9bb0ef7a`.
- Smoke verified captured agent output and absence of a Docker socket, database
  URL, bootstrap token, E2B key and Docker client certificate. An attempted Docker
  API request without a client certificate was denied. Model requests went to a local fixture;
  the official verifier rejected the deliberately unsolved task as expected.
- All 10 selected task IDs and difficulty/category metadata were checked against
  downloaded `terminal-bench@2.0` (89 tasks total).
- `pip check` passed independently for service and Harbor environments.
- HTTP `/health` returned `{"status":"ok"}` on localhost:8080.

## Remaining live performance validation

No LLM credential was configured when these checks ran. No live agent baseline,
live LLM proposal, measured full-subset runtime or benchmark improvement is claimed.
Configure the provider key, recreate services, then run:

```sh
python test_client.py --task-ids fix-git --max-iterations 0
python test_client.py
```

Record actual outcomes, proposals, scores, runtime and provider usage afterward.
A plateau is valid; fabricated improvement is never substituted for measurements.
