# Handoff for the architecture chat

The user is working on a take-home Agent Optimization Service. The interviewer
asked: "i see. would love to see a design of your arch as well whenever you have
it ready". The user has about two hours remaining and wants architecture work in
this chat while the original chat owns agent development and benchmark runs.

## Deliverable

Coordination update: automatic multi-round package optimization is now implemented
and deployed (`service/searches.py`, `service/search_api.py`, migration 005), with
66 passing tests. Read `docs/AUTOMATIC_OPTIMIZATION.md`. A bounded live run is in
progress; HANDOFF.md has its ID/state. Include this layer in the architecture:
workers advance transactional search states between jobs; they schedule baseline,
proposal and comparison jobs, select a development winner, repeat within limits,
then run optional final checks. No extra broker/scheduler process was added.
The older statement below about no automatic package selection describes only
standalone experiments at the published `ce5bd08` checkpoint. New optimization
runs select their own incumbent; they do not promote it into deployment defaults.

Create `docs/ARCHITECTURE.md`: a reviewer-facing description of the **implemented**
system, grounded in the source. Include a component/deployment diagram, the
submission-to-result sequence with crash recovery, and a compact data/ownership
diagram. Mermaid is appropriate; put any additional diagram assets under
`docs/architecture/`. Explain the main tradeoffs and distinguish current behavior
from proposed future scaling. Finish with a short explanation the user can send
to the interviewer. Draft that message; do not send it yourself.

Start from the existing Mermaid diagram in README's "Design decisions" section.
The task is to explain and visualize the service accurately, not redesign it or
add infrastructure during this documentation pass.

## Read in this order

1. `HANDOFF.md`: current implementation, run IDs, constraints and environment.
2. `README.md`: setup, task selection, API/roles, existing architecture sketch.
3. `docs/EXPERIMENT_PLATFORM.md`: package contract, scheduling, recovery, capacity.
4. `docs/OPERATIONS.md`: measured worker/container failure and cache behavior.
5. `docs/PACKAGE_EXPERIMENT.md`: completed tools/context/skills comparison.
6. Source files below to verify diagram arrows, data ownership and claims.

Assignment pages are `Question page 1.png` and `Question page 2.png` in the root.
`docs/EXPERIMENT_PLATFORM_PLAN.md` contains a historical starting-point gap analysis;
its gap table is not the current implementation. The complete JSON result files
are available if needed; reading their large embedded sources is not necessary
for an initial architecture explanation.

## Source map

| Concern | Source |
|---|---|
| Deployment, networks, volumes and credentials | `compose.service.yaml`, `service/Dockerfile` |
| HTTP API, authentication and organization roles | `service/api.py`, `service/campaign_api.py` |
| Experiment plans, versions, aggregation and profiles | `service/campaigns.py`, `service/packages.py` |
| Queue admission, leases, reservations and fenced writes | `service/queue.py`, `service/pool.py` |
| Workers, retries, watchdog and cleanup | `service/worker.py`, `service/runner.py` |
| Harbor launcher, cache and sandbox execution bridge | `service/harbor_cli.py`, `service/harbor_agent.py` |
| Sandbox runtime, model requests, tools and skills | `service/runtime.py` |
| LLM proposals and evidence | `service/package_optimizer.py`, `service/optimizer.py`, `service/evidence.py` |
| Persisted entities and relationships | `service/migrations/001_initial.sql` through `004_experiment_queue_indexes.sql` |
| Client workflows | `test_client.py`, `experiment_client.py` |
| Recovery evidence and assertions | `tests/operational_proof.py`, `tests/test_campaigns.py`, `tests/test_service.py` |

## Facts the design must preserve

- FastAPI handles HTTP; PostgreSQL stores both durable state and queue records.
  There is no Redis/Celery broker. External execution happens outside transactions.
- The API has neither Docker access nor inference credentials. Workers call the
  optimizer model and launch Harbor, which controls a dedicated Docker engine
  over mutual TLS. Agent code and its inference/tool loop run inside task sandboxes.
- Immutable harness packages include Python modules, tools, context logic and
  declared skill procedures/resources. The API/worker validate code as data;
  only a sandbox imports it. Versions record lineage, diffs and hashes.
- Distinguish the two workflows. `/jobs` provides the original iterative
  single-module optimization with strict improvement/no-regression acceptance.
  Package experiments compare frozen independent candidates; each task/repetition
  is its own durable job. There is no automatic package promotion or repeated
  package search algorithm beyond these independent proposals/comparisons.
- Development trials precede held-out trials; held-out jobs cannot supply proposal
  evidence. The fixed profile applies to every version within a comparison.
- Shared task-slot admission bounds all managed workers. Leases and attempt tokens
  fence writes; reservations remain until scoped cleanup. Execution is at least
  once, so interrupted work/provider calls may repeat; completed trials are kept.
- A separate watchdog handles blocked lease heartbeats. Cleanup fails closed on
  engine errors. Parent reconciliation repairs missed final aggregation. Cache
  publication uses a process lock and staged rename to avoid partial task copies.
- Organization admins manage members and see organization activity; members see
  their own resources. Full raw artifacts are on a private shared volume; bounded
  excerpts and iteration history are available through authorized API routes.
- The tested deployment uses one Docker engine and shared volumes, two workers,
  and two admitted slots. It is not multi-host or HA. Docker shares a kernel;
  E2B/microVM isolation, scoped inference proxy, object storage and more advanced
  admission/promotion are future work. Task agents can access their inference key.

## Evidence and publication snapshot

PR: https://github.com/neosigmaai/auto-harness/pull/32
Branch: `feat/agent-optimization-service`; published commit `ce5bd08`.
58 PostgreSQL tests and GitHub CI passed. The real four-worker/two-slot drill
recovered worker death, rejected stale writes, preserved completed history and
handled cancellation/sandbox failure with no leftovers. Its synthetic 30.95-second
timing is not production benchmark throughput.

The 36-trial package comparison completed in 30m11s with zero runner errors:
baseline and skills 6/6 development + 3/3 held-out; tools and context 5/6 + 3/3.
Both regressions were Nginx formatting; no pass-rate improvement is established.
Actual traces confirm tool/context/skill mechanisms. Existing held-out tasks were
used in a prior experiment, so do not describe them as newly unseen tasks.

At handoff creation there are no queued/running jobs or task reservations. The
original chat may start more work later; that snapshot is not permission to
restart services. It owns benchmark scheduling and shared deployment changes.

## Coordination between the two chats

Work in `C:\Users\Calvin\dev\auto-harness`. Own `docs/ARCHITECTURE.md` and optional
`docs/architecture/` assets. Read other files for context. Let the original chat
integrate README/PR links and any implementation corrections you identify.

Do not restart/rebuild Docker, change `.env`, modify service/agent code, launch
benchmarks or reset shared state. Do not switch branches in this shared checkout.
Avoid broad Git staging such as `git add .`; keep any commit limited to files you
own. Read status before writing so concurrent work is not overwritten. Record
architecture/code discrepancies in your response for the original chat to assess.
Never print credential values from `.env` or `workspace/client-*.json`.
