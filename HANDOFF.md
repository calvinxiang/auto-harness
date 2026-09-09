# Agent optimization service handoff

Current snapshot: 2026-09-08. Implementation, live package comparison and final
local deployment are complete. This file supersedes the earlier chronological
notes, preserved locally in workspace/handoff-history-before-package-completion.md.

## Two-chat coordination

Active work after the user's correction: do not run a Terra comparison; that was
only an interviewer question. No Terra benchmark was submitted. Automatic
multi-round package optimization is implemented and deployed at local checkpoint
`46e7d4c`: `service/searches.py`, `service/search_api.py`, migration 005, and
`test_client.py --optimize` / `optimization_client.py`. All 66 tests pass, including
real HTTP client/resume and two-round selection with rejection of regressions.
Read `docs/AUTOMATIC_OPTIMIZATION.md`. The architecture chat should include this
implemented layer before finalizing; older statements about no package search
describe the earlier `ce5bd08` checkpoint. Standalone experiments still do not
select a winner; optimization runs select their internal next parent automatically.

LIVE RUN: caab9717-1377-4def-afeb-0a23bdc357f0, organization
b10dc6fe-59b0-4b6b-885a-18e2c6444287. Started about 20:10 Toronto. Client session
59328. Resume state `workspace/automatic-package-state.json`, final output
`workspace/automatic-package-results.json`. Do not recreate workers during this
run. Development tasks configure-git-webserver/extract-elf, one repetition;
dimensions context/skills/control, max_rounds=2, patience=2; final held-out task
large-scale-text-editing. At most six proposals and 24 trials, including the
explicit final development confirmation before held-out evaluation. Astra
Responses xhigh/32768, GPT-5.4 medium optimizer; no model changes. Runtime and
initial package are the same frozen version used in the earlier experiment.
No live outcome is claimed yet. Remaining: inspect automatic decisions/results,
export a sanitized full report, integrate docs/PR and verify final CI.
Post-checkpoint hardening isolates a malformed controller state to its own run,
cancels its unfinished children and lets ordinary work continue. 67 tests pass;
new images are built but must be deployed only after the live run finishes.

The user has about two hours left and wants a separate chat to document the
architecture while this original chat owns agent work and benchmark scheduling.
Read `docs/ARCHITECTURE_HANDOFF.md` for the architecture brief and source map.
That chat owns `docs/ARCHITECTURE.md` and `docs/architecture/` assets; this chat
integrates shared README/PR edits and manages the running deployment. Avoid branch
switches and broad Git staging in the shared checkout. At this handoff, all prior
benchmarks have finished and no jobs are queued/running. Published checkpoint
`ce5bd08` is on PR #32 and its GitHub CI passed.

## Objective and user direction

Complete the take-home HTTP service, durable async jobs, sandboxed TerminalBench
execution, LLM optimization/history, organization roles, README/test_client.py
and reviewable PR. Question images are Question page 1.png and Question page 2.png.
The interviewer prioritizes the full harness search space (tools, skills, context,
control flow) and solid experiment infrastructure over an immediate score gain.
The user authorized implementation, paid model calls and updating the existing PR.
Do not ask again for routine continuation or publication of this work.

## Current implementation

- FastAPI/PostgreSQL, separate workers, leases/fencing, tenant/owner access checks.
- Immutable multi-file harness packages with skill assets, parent lineage, diffs,
  manifest/file/runtime/runnable hashes; source loads only inside Docker sandboxes.
- Queued LLM package proposals with development evidence, validation and failure
  retention. Tools/context/skills dimensions plus optional review feedback.
- Durable experiment plans, frozen model/resource profiles and one job per task
  trial. Completed trials survive worker death. Held-out work waits for development.
- Shared task-slot admission, reservations retained until scoped cleanup, independent
  lease watchdog, cancellation, retry fencing and periodic parent reconciliation.
- Harbor shared cache uses cross-process locking and atomic staged publication.
- Original iterative /jobs optimization loop still proposes single-module changes
  and stops on strict no-regression rejection or its iteration limit. Package
  experiments explore independent candidates; no automatic package promotion.
- Resumable test_client.py --experiment and operator artifact inspection.
- One Docker engine and shared artifact volume; no multi-host/HA, E2B provider,
  scoped inference proxy, statistical promotion or production VM isolation.

Read README.md, docs/EXPERIMENT_PLATFORM.md and docs/PACKAGE_EXPERIMENT.md first.
The historical gap analysis in docs/EXPERIMENT_PLATFORM_PLAN.md is superseded.

## Completed live package comparison

Experiment b8786371-fef7-4617-9736-339126965685, organization
b10dc6fe-59b0-4b6b-885a-18e2c6444287. Completed 36 trials, 23:16:01 to 23:46:12 UTC
(30m11s); one attempt each, zero runner errors, zero supervisor timeouts.

Development: fix-git, log-summary-date-ranges, nginx-request-logging, two repetitions.
Held-out: cancel-async-tasks, openssl-selfsigned-cert, large-scale-text-editing, once.
Baseline 6/6 development + 3/3 held-out; skills 6/6 + 3/3; tools 5/6 + 3/3;
context 5/6 + 3/3. Both regressions were Nginx formatting in repetition index 1.
No pass-rate improvement, no promotion, no scored retries or outcome adjustments.
The existing held-out split appeared in a prior experiment: do not call it unseen.

Actual request/trace evidence: tools 39 structured results; context eight compacted
outputs (one 6062 -> 1936 chars); skills discovery/loading in all nine skills trials.
These establish mechanism execution, not compliance or causal improvement.
Proposal model GPT-5.4 medium; all agents GPT-6 Astra Responses xhigh / 32768 tokens,
300s / 80 calls / 1 CPU / 2 GB, two workers and two globally admitted slots.
Six generation attempts in two review rounds: initial tools/skills reviewed out
for fixture special cases and mixed changes; context schema failed. Three focused
replacements reviewed before scores. Initial raw malformed response was discarded;
new failures now preserve bounded raw output. No held-out evidence fed proposals.

Public exact packages, proposals, versions/hashes, histories and artifact metrics:
docs/package-experiment-results.json. Readable analysis: docs/PACKAGE_EXPERIMENT.md.
Ignored full client report: workspace/package-search-v2-results.json.
Resume state: workspace/package-search-v2-state.json (already complete; no paid work
will be rerun). Inspector output: workspace/package-mechanisms.json. Ignored
workspace/export_package_report.py verifies hashes and checks credentials before export.
Runtime at commit 4d150e8 matches all four scored versions. Later changes harden
cache publication, parent recovery and local client defaults; they do not edit
frozen source or historical scores.

## Verification and deployment

58 PostgreSQL tests pass. Offline package checks load helper modules and skills;
reject crashing code, malformed tool history and infinite loops.
Real cache negative control reproduced a partial read; protected concurrent reads
and killed-writer recovery passed against pinned Harbor 0.1.45.
Final worker drill: four processes/two slots, worker killed with remote container
alive, retry fenced, completed history retained, duplicate requests/cross-tenant
access/cancel/sandbox interruption checked. Passed in 30.95s, peak two containers,
zero leftovers. These are synthetic infrastructure timings, not LLM throughput.
Details and retained earlier run: docs/OPERATIONS.md and operational-results.json.
Final drill database harness_ops3_test; use a NEW empty test DB for another drill.

Final real Harbor/local-model smoke df853da2-3109-4aeb-b7ec-fada4f9e7ca8 passed
new-tool dispatch, trace capture and isolation through the new cache-aware launcher.
It deliberately left its benchmark task unsolved and used no paid LLM.
Final images deployed: API plus two workers, migrations through 004 applied.
http://127.0.0.1:8080/health returned ok; zero queued/running jobs, reservations
or task containers remained. Use 127.0.0.1: localhost had an observed 2s/request delay.

## Earlier results and latest user questions

Original-policy Astra job 8ef90c0f-134d-4009-918c-0dcc64a92418: 7/10, 13m01s,
89 calls. docs/FLAGSHIP_EXPERIMENT.md and flagship-results.json.
Mini code-optimization job 4f31b776-a891-4bac-a0c3-3b9f07e14387: baseline 4/10,
8m28s, 352 calls; candidate 1/10 rejected. Full two-evaluation job took 17m12s.
User asked why Astra looked faster: it was one evaluation versus mini's complete
optimization run. Mini's single baseline was faster, despite many more calls.
User asked about Terra: hypothesis that it could outperform 4.1 mini; no Terra
experiment has been run or requested explicitly. Future transfer comparison should
reuse frozen packages and budgets in a separately labeled model experiment.
Earlier repeated mini comparison: 46 trials, development 2/20 versus 4/20, held-out
1/3 versus 0/3; regression and one agent-caused filesystem failure retained. Read
docs/EXPERIMENT_RESULTS.md. Do not promote that candidate or tune on held-out results.

## Environment, credentials and publication

Windows PowerShell, Python 3.13, Docker Desktop Linux containers. Docker commands
require escalation; docker exec/compose are approved. Git requires
-c safe.directory=C:/Users/Calvin/dev/auto-harness and escalation for writes.
Never print or commit .env or workspace/client-*.json. Keys and bootstrap remain
in ignored .env. Unrelated Supabase containers and assignment images stay untouched.
Current local .env has Astra trial settings and GPT-5.4 medium optimizer settings.

Branch feat/agent-optimization-service. Existing PR:
https://github.com/neosigmaai/auto-harness/pull/32
Fork calvinxiang/auto-harness; upstream push unavailable. Publication authorization
persists. Exact PR text: docs/PR_DRAFT.md. Before publishing, run ignored
workspace/check_staged.py to check staged credentials and git diff --cached --check.
Push branch to https://github.com/calvinxiang/auto-harness.git, then run
python workspace/github_submission.py update-pr under escalation. The helper uses
Git Credential Manager in memory and never prints tokens. Use its verify action
for current remote HEAD/checks. Do not merge or send separate interviewer messages.

Useful checks:

```sh
docker compose -f compose.service.yaml run --rm --no-deps -e DATABASE_URL=postgresql://harness:local-development-only@db:5432/harness_test api python -m pytest -q
docker compose -f compose.service.yaml run --rm --no-deps worker python -m tests.sandbox_smoke
docker compose -f compose.service.yaml run --rm --no-deps worker /opt/harbor/bin/python -m tests.cache_lock_smoke
```

Avoid the original all-task optimization PROGRAM.md loop. The legacy operator
service.experiments CLI bypasses global admission; do not run it alongside managed
experiments. Use the durable API for new comparisons.
