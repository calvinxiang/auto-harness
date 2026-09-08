# Agent optimization service handoff

## Latest state: flagship baseline complete (2026-09-08)

User authorized trying the strongest available model, with price not a constraint.
The key lists `gpt-6-astra`; live Responses tool-call and two-turn protocol probes
passed. Official guidance requires Responses for Astra tool calls. Added explicit
agent API, reasoning effort and output budget settings, Responses translation with
native reasoning/phase replay, and incomplete-response diagnostics. Policy code,
prompt, tools, dataset, 300s task limit, 80 calls and concurrency 2 are unchanged.
37 PostgreSQL tests and all offline sandbox checks pass. Images rebuilt and deployed.

Ignored `.env` now sets AGENT_MODEL=gpt-6-astra, AGENT_API=responses,
AGENT_REASONING_EFFORT=xhigh, AGENT_MAX_OUTPUT_TOKENS=32768. Optimizer defaults are
unchanged and the active job has zero proposals. This is a model/configuration
comparison, not a strict model-only ablation: Responses and a larger reasoning/output
allowance are required to exercise the new model properly. Never print credentials.

Full 10-development-task API job: `8ef90c0f-134d-4009-918c-0dcc64a92418` succeeded:
**7/10 passed**, 3 failed, zero runner errors, 13m01s, 22:25:45 to 22:38:46 UTC.
Organization: `b10dc6fe-59b0-4b6b-885a-18e2c6444287`. Complete client output is in
`workspace/live-astra-baseline.json`; corresponding local client credentials are
ignored. Policy equality with prior baseline was verified. Public exact runnable
source, configuration, hashes, usage and task results: `docs/flagship-results.json`.
Readable analysis: `docs/FLAGSHIP_EXPERIMENT.md`. No optimizer, retries or score edits.

Failed tasks: configure-git-webserver (local push/HTTP worked, verifier SSH flow
failed; login setup assumptions differ), extract-elf (0% address coverage and
305.3s execution consistent with watchdog; last metadata is incomplete), and
nginx-request-logging (7/8 checks pass; extra request-time field followed user agent,
while verifier requires user agent last). These are documented without changing
the official rewards. Earlier mini baseline totals: 4/10, 1/10, 1/10; nginx passed
in the earlier 4/10 run. This single result is not evidence of uniform improvement.

Usage: 989,676 input / 47,617 output tokens, including 16,080 reasoning tokens;
89 model calls. Full tests pass after the logging-only deep-copy fix; this fix did
not change the running job's frozen source. The local fixture smoke explicitly
selects Chat Completions so the Astra environment does not break fixture requests.
Final real Harbor/local-model smoke `354cf62f-6c81-409c-bdc6-f6897f2f3b74`
passed tool dispatch, trace collection and isolation checks. Final API/worker
images are deployed and `/health` is ok. This follow-up is included in PR #32.

## Prior completed code experiment (2026-09-08)

The implementation and requested controlled experiment are complete. All five
milestones have implementation paths and coverage. **32 PostgreSQL tests pass**;
positive and negative offline sandbox checks passed. Updated API/worker images are
running, `/health` is ok, there are no active API jobs and no remaining task containers.

Experiment `bb881cf2-6f45-4ff3-9149-1031c0e2274f` completed all 46 task executions
in 46m27s. Development baseline: 2/20; candidate: 4/20. Held-out baseline: 1/3;
candidate: 0/3. Both development pairs had regressions. One candidate destroyed its
task filesystem; its raw missing-reward error is preserved and explicitly assessed
as zero, without retry. The other 45 executions have verifier results. Do not
recommend this candidate as an improved agent or tune using the held-out results.
No existing API job history or best pointer was changed.

Read docs/EXPERIMENT_RESULTS.md and docs/code-experiment-results.json for the
complete protocol, review history, source, hashes, per-task outcomes, usage and limits.
The experiment used a GPT-5.4 optimizer, while both benchmark agents used 4.1-mini.
The ordinary API defaults remain 4.1-mini for both roles; `.env` was not changed.
Implementation checkpoint: `7044317`; complete results: `9fee270`.

The user explicitly requested opening a PR after evaluation so the interviewer can
review it. GitHub device sign-in succeeded as `calvinxiang`; fork
`calvinxiang/auto-harness` is ready. Upstream push permission is false. Final
publication is complete: https://github.com/neosigmaai/auto-harness/pull/32, from
`calvinxiang:feat/agent-optimization-service`. No further user authorization is
required. Do not print credentials. Earlier status notes below
are a historical work log, superseded by this section.

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
- Prior prompt-only 10-task job completed: `efbafbbc-8ceb-4607-9cf6-d75bb29cdd73`, org
  `bae13d3c-d367-46ca-baf9-3f7b61e1f7c6`, output `workspace/live-optimization.json`.
  Duration 16m47s; baseline 1/10, candidate 4/10. Both used gpt-4.1-mini and had zero
  runner errors. Candidate rejected because nginx-request-logging regressed, so
  best_score remains 0.1 and stop_reason is no_improvement. One automatic proposal
  strengthened command-exit-status checks/error recovery. No manual prompt edits.
  Full results retrieved through client; sanitized summary: docs/live-results.json.
- Service implemented in `service/`, Compose in `compose.service.yaml`, client in
  `test_client.py`, docs in README and `docs/VALIDATION.md`.
- API running at http://localhost:8080 (docs at /docs), database migrations applied.
- 32 PostgreSQL tests pass, including the actual client over real HTTP with fixtures.
- Real Harbor/Docker installed-agent smoke passed with local model fixture, and
  live OpenAI baseline/optimization completed. All 10 selected task IDs validated.
- E2B is saved for future use; the service currently implements Docker only.

## Implementation direction

- FastAPI + PostgreSQL, separate worker, PostgreSQL queue with leases and fencing.
- Separate Compose project; API has no Docker socket. Worker controls Harbor.
- Fixed Harbor adapter installs/runs agent Python inside each task sandbox.
- Docker task environments with dedicated Docker-in-Docker engine. Worker connects
  using mutual TLS. API has no engine access. E2B adapter is deferred.
- User corrected prompt-only scope. Optimizer now proposes a complete Python agent
  module (tools, context handling, loop, helpers and prompt), with a fixed supervisor.
  Persist module, runnable bundle, diff, hash, proposal, validation and results.
  New migration 002 adds nullable fields; old prompt-only history remains readable.
  Static checks never execute proposed code. Offline preflight imports/exercises it
  in a separate container before benchmark execution. See docs/AGENT_CODE.md.
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

## Historical work log: controlled code experiment

User authorized generating an evidence-backed proposal, reviewing it, comparing
frozen versions repeatedly and using held-out tasks. This is now running as an
operator experiment, separate from API jobs and promotion decisions.

- Experiment: `bb881cf2-6f45-4ff3-9149-1031c0e2274f`.
- Private report: `/artifacts/experiments/<id>/report.json`; execution artifacts
  under that experiment's `execution/` directory, outside the queue reaper's sweep.
- Source baseline: best iteration of job `4f31b776-a891-4bac-a0c3-3b9f07e14387`.
- Optimizer for this proposal: `gpt-5.4-2026-03-05`, medium reasoning. Benchmark
  agent remains `gpt-4.1-mini`; both versions keep the original runtime and limits.
  `.env` still uses the cheaper optimizer default; experiment overrides are explicit.
- Generated change: explicit `finish` tool citing a successful command in actual
  history. General mechanism, no task-specific solutions. Write detection and
  command matching are heuristics; a successful command does not prove correctness.
- Review probes passed: accepts silent shell checks and Python assertions;
  rejects tested stale, failed and absent checks. Sandbox contract passed.
- Prior GPT-5.4 proposal `0ade3a03-d692-471b-8ef0-ac50019175ff` rejected during
  review because its command whitelist rejected valid silent checks. Revision
  `1bb0c3ba-75d0-4719-a050-27932a5a549c` failed response validation before execution;
  exact cause/usage were unavailable. Current retry allows more output and saves
  safe failure diagnostics. Earlier 4.1-mini proposal `54225391-049e-4381-bd1f-f05c26557632`
  repeated the unsupported empty-response idea and was not benchmarked.
- Frozen plan: two development repetitions per version, alternating order,
  followed by one pair on `cancel-async-tasks`, `openssl-selfsigned-cert`, and
  `large-scale-text-editing`: 46 task executions total. Held-out tasks were chosen
  from metadata before any results. Do not tune using their outputs.
- Started evaluation at 21:08:22 UTC on September 8. Development repetitions:
  baseline 1/10 and 1/10; candidate 1/10 and 3/10, including the explicit zero-score
  assessment below. Aggregate baseline 2/20, candidate 4/20. Held-out baseline
  finished at 1/3. The final held-out candidate run is active as of 21:49 UTC.
  The historical baseline was 4/10; do not mix it into the fresh repetitions.
  Resume/checkpoint command (only after the current process ends):

```sh
docker compose -f compose.service.yaml run --rm --no-deps worker python -m service.experiments evaluate --experiment-id bb881cf2-6f45-4ff3-9149-1031c0e2274f --repetitions 2 --review-reason "Reviewed completion verification; preserve frozen comparison"
```

Do not launch a duplicate while it is running. Record all scores, per-task
regressions, timings and usage in docs after completion; do not claim a reliable
gain from a favorable single trial. No API history or best pointer is changed.
Uncommitted changes add evidence counts, reasoning settings, the experiment tool,
safe provider diagnostics, and an offline contract driver supporting a finish tool.
The driver is separate from scored runtime, with its own persisted source hash.
32 tests and both positive/three negative real sandbox contract checks passed.
First candidate had an agent-caused verifier error on `configure-git-webserver`:
an unquoted heredoc expanded a cleanup variable to empty, deleting the task's
filesystem. Trace evidence is clear; do not retry this away. Added an explicit
`assess-agent-failure` command which preserves `original_results`, assigns zero,
marks the run `assessed`, and lets resume skip it. The assessment is recorded and
the experiment resumed without rerunning this task. Assessed run ID:
`79e7d6aa-8f52-4dca-bbe9-dbf9d2ff8ffe`. All current images include this command.
See docs/EXPERIMENT_RESULTS.md and the tool's help. Keep the candidate frozen.
Implementation committed as `7044317`; final result/documentation edits remain.
New images are built; API/worker production containers still use the prior image.
After evaluation, export the trace-free public report using ignored
`workspace/export_experiment.py` (copy to the worker and run there), copy
`public-results.json` to `docs/code-experiment-results.json`, and finish docs/results.
Then recreate API/worker, verify health, scan staged changes and commit.

## Publication setup notes

The user explicitly asked to open the PR after this run so the interviewer can
review it. Git Credential Manager device sign-in succeeded as `calvinxiang`.
Upstream still has push=false, so the writable fork `calvinxiang/auto-harness` has
been prepared. Do not request authorization again. Do not expose stored credentials.

Ignored `workspace/github_submission.py` supports `status`, `prepare`, `create-pr`.
It reads credentials into memory via GCM, uses only GitHub's API, checks the pushed
branch matches local HEAD, and reads the exact title/body from docs/PR_DRAFT.md.
Run under escalation so Windows Credential Manager is available. After final
commit, push `feat/agent-optimization-service` to the fork (add a submission remote
or use its public HTTPS URL), then run `create-pr`. No PR is open yet.
The earlier access blocker documented below is historical and now resolved via fork.

## Prior checks and delivery

1. Code extension has 24 passing tests and an added-tool sandbox smoke pass
   (`021c516b-3fde-4cc8-a30d-93909d6ed194`). API/worker recreated; migration 002
   confirmed applied. Initial job failed before inference because migrate image was
   stale; always build migrate as well as api/worker (or use up --build).
   Live job `4f31b776-a891-4bac-a0c3-3b9f07e14387` completed, organization
   `feb7c5c6-e228-417c-ae5e-09131f119ce0`, output workspace/live-code-optimization-v2.json.
   Ten tasks, max_iterations=1, 17m12s, zero runner errors. Baseline 4/10, candidate
   1/10, rejected with no_improvement; best remains 0.4. Candidate retries an empty
   no-tool-call response. The hypothesis is unsupported: baseline traces had 352
   assistant messages and zero such responses. Report this honestly.
   Structured results/code/diff: docs/live-code-results.json.
   Code extension committed as 17a7145. Follow-up optimizer input now includes
   runtime metadata and passing-task context; built, 24 tests pass, and services
   recreated after live job completion. Its effectiveness has not been benchmarked.
2. Implementation committed as `c086b76`. Git push failed: local GitHub credential
   rejected. Connected GitHub app login is `calvinxiang`; upstream metadata reports
   push=false. `calvinxiang/auto-harness` returned 404. Publishing needs a writable
   fork/access and usable authentication. Exact PR draft is docs/PR_DRAFT.md. No PR
   was opened. Do not include `.env` or local assignment images.
3. Live single-task baseline and full client run are complete; results, runtime and
   token usage recorded in docs/VALIDATION.md. README and PR draft updated.
4. The first code-optimization live job is complete. Finish the controlled experiment
   above and publish the branch/PR once GitHub access is available. Do not confuse
   prior prompt-only scores with the editable-code implementation.

Temporary `harness-dev` container was removed. Experiment task containers are active.
Existing Supabase containers remain untouched. Original README: docs/UPSTREAM_README.md.

Useful checks:

```sh
docker compose -f compose.service.yaml up --build -d
docker compose -f compose.service.yaml run --rm --no-deps -e DATABASE_URL=postgresql://harness:local-development-only@db:5432/harness_test api python -m pytest -q
docker compose -f compose.service.yaml run --rm --no-deps worker python -m tests.sandbox_smoke
```

The test database `harness_test` already exists. Tests refuse other database names.
Do not run an original all-89-task baseline or the optimization PROGRAM.md loop.
