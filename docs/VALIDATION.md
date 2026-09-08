# Validation record

Latest platform checks: **57 PostgreSQL tests pass**, including experiment
ownership/idempotency, held-out gating, immutable package recovery, capacity
admission and cleanup failures. Real offline Docker checks load Python helpers
and skill assets. Four worker processes under two slots passed the crash,
cancellation and sandbox-failure drill: [OPERATIONS.md](OPERATIONS.md).
Earlier test counts below describe their historical checkpoints.

Recorded 2026-09-08 on Windows Docker Desktop using Linux containers.

## Flagship baseline

API job `8ef90c0f-134d-4009-918c-0dcc64a92418` completed the 10-task development
subset with `gpt-6-astra`, Responses, `xhigh` reasoning and a 32,768-token output
allowance. Original policy unchanged; zero proposals. **7 passed, 3 failed, zero
runner errors**, in 13m01s. Source equality with the earlier baseline and runnable
source hashes were checked during export. No scores were adjusted or tasks retried.
The three failures concern the SSH/webserver workflow, binary extraction near the
watchdog limit, and exact Nginx log formatting. The report distinguishes observed
verifier failures from interpretations of their causes.

37 PostgreSQL tests pass. New cases cover native Responses reasoning/phase replay,
tool-result pairing, context/schema edits, explicit model budgets and rejection of
incomplete output before tool dispatch. Live protocol probes and positive/negative
offline sandbox checks passed. The final logging-only request-copy fix was tested
separately; the benchmark retained its original frozen runtime snapshot.
The final real Harbor/local-model smoke also passed: run
`354cf62f-6c81-409c-bdc6-f6897f2f3b74` verified the added-tool dispatch, trace
collection and sandbox isolation while deliberately leaving its task unsolved.

See [FLAGSHIP_EXPERIMENT.md](FLAGSHIP_EXPERIMENT.md) and
[flagship-results.json](flagship-results.json) for results and comparison limits.

## Repeated and held-out code experiment

Experiment `bb881cf2-6f45-4ff3-9149-1031c0e2274f` completed 46 task executions in
46m27s. A reviewed GPT-5.4 proposal added a finish tool; both scored agents retained
`gpt-4.1-mini` and the same runtime, dataset and limits. Fresh development baseline
scores were 1/10 and 1/10; candidate scores were 1/10 and 3/10. Held-out baseline
passed 1/3, candidate 0/3. Each development pair contained a task regression.

One candidate destroyed its own task filesystem through an unquoted heredoc.
The resulting missing verifier reward is preserved and explicitly assessed as a
zero-score agent failure, without retrying it. The other 45 executions produced
verifier results. The candidate is not recommended for promotion, and no API
job's history or best pointer was changed by the operator experiment.

See [EXPERIMENT_RESULTS.md](EXPERIMENT_RESULTS.md) for the review process,
per-task outcomes, timings, usage and limitations, and
[code-experiment-results.json](code-experiment-results.json) for the actual code
and structured record. The updated API/worker are running, `/health` returns ok,
the API queue had no active jobs, and no task containers remained at the end of
that experiment.

## Python code optimization extension

The optimizer now changes the complete agent policy module: tool definitions,
dispatch, context handling, helper functions, prompt and the agent loop. The
runtime/model configuration and benchmark remain service-controlled.

- 37 PostgreSQL tests pass, including preservation of invalid code proposals and
  the best version, source diffs, validation results, tool-message pairing, static
  checks that never execute proposed code, and independent trace preservation.
  Additional checks cover trace observations, fixed comparison settings, alternating
  repetitions, evaluation resume, held-out feedback exclusion, reasoning parameters
  and safe diagnostics for truncated optimizer responses.
  Explicit agent-failure assessments retain raw errors, never award a pass,
  require a stopped experiment and do not rerun resolved task attempts.
- Offline preflight and a real Harbor task successfully executed a code change
  adding a tool and dispatch branch. Run ID:
  `021c516b-3fde-4cc8-a30d-93909d6ed194`. This used a local HTTP model fixture,
  checked task isolation, and deliberately left the benchmark task unsolved.
- Real offline containers rejected import-time failure and broken tool history
  (exit 1), and terminated an infinite loop (exit 124) under the 15-second timeout.
  Reproduce with `docker compose -f compose.service.yaml run --rm --no-deps worker python -m tests.sandbox_validation`.
  The current driver also accepts both text completion and an explicit finish tool;
  both completion protocols and all three rejection cases passed again after the
  driver was separated from the scored runtime.
- Live job `4f31b776-a891-4bac-a0c3-3b9f07e14387` completed a 10-task baseline and
  one automatically proposed code candidate using `gpt-4.1-mini` for both roles.
  It ran from 20:20:54 to 20:38:06 UTC, **17m12s** end-to-end. Both iterations passed
  static/offline validation and completed with zero runner errors. Source, code
  diff, validation and full history were retrieved through `test_client.py`.
- Baseline: **4/10**, 8m28s. Candidate: **1/10**, 8m31s. The candidate was rejected,
  retaining best score **0.4** with stop reason `no_improvement`.
- An initial deployment attempt (`3734951a-e9b5-470f-a36e-6ae135b5fea6`) stopped
  before inference because the separate migration image was stale. Rebuilt all
  images and confirmed migrations 001 and 002 before resubmitting. This is not a
  benchmark result.

| Task | Baseline | Code candidate |
|---|---|---|
| fix-git | Passed | Failed |
| regex-log | Failed | Failed |
| git-leak-recovery | Passed | Failed |
| log-summary-date-ranges | Passed | Passed |
| build-cython-ext | Failed | Failed |
| configure-git-webserver | Failed | Failed |
| fix-code-vulnerability | Failed | Failed |
| extract-elf | Failed | Failed |
| nginx-request-logging | Passed | Failed |
| sqlite-db-truncate | Failed | Failed |

The generated code retries empty no-tool-call responses instead of returning
immediately. Inspection of all baseline traces found 352 assistant messages and
**zero instances of that condition**. The diagnosis was therefore unsupported by
the recorded baseline. The score decrease cannot establish that this change caused
the regressions; single-run model sampling and environment variation remain factors.
This validates code proposal/application/evaluation and rejection, not successful
agent improvement or generalization.

| Usage | Input tokens | Output tokens |
|---|---:|---:|
| Baseline agent calls | 4,725,022 | 25,334 |
| Candidate agent calls | 2,316,869 | 20,937 |
| Optimizer proposal | 26,966 | 812 |

See [live-code-results.json](live-code-results.json) for both Python modules, the
actual diff, validation, model settings, per-task results, source hashes and usage.
The full local client output is ignored `workspace/live-code-optimization-v2.json`.
No benchmark/preflight containers remained after completion.

A follow-up adds runtime metadata (calls, tokens, stop reason) and passing-task
context to optimizer input, and asks it to ground changes in concrete recorded
evidence. It passed the 24-test suite and was deployed after the job finished.
The later controlled comparison above evaluated a reviewed proposal using the
enriched feedback; it did not isolate the effect of the feedback change itself.

The historical measurements below belong to the earlier prompt-only implementation.

## Initial implementation checks

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

## Live single-task baseline

After the user configured an OpenAI key, the installed agent ran `fix-git` using
`gpt-4.1-mini` through the actual service and `test_client.py`.

- Job: `4b0a2329-8f05-495d-ace8-07e4d9a272ab`.
- End-to-end duration: approximately 51 seconds.
- Service outcome: succeeded; one baseline iteration persisted.
- Task outcome: failed, reward 0. The agent recovered a dangling Git commit but
  resolved the about-file conflict incorrectly; the other verifier assertion passed.
- Agent usage: 13,835 input tokens and 536 output tokens.
- A real cross-organization API request was denied with HTTP 404.

This establishes live model execution, verification, persistence and retrieval.
It is not an improvement claim.

## Initial prompt-only optimization run

Job `efbafbbc-8ceb-4607-9cf6-d75bb29cdd73` completed through `test_client.py` using
`gpt-4.1-mini` for both the agent and optimizer. It ran from 19:23:02 to 19:39:49 UTC:
**16 minutes 47 seconds** including baseline, automatic proposal and candidate.
The service succeeded on its first worker attempt, with zero Harbor runner errors
in either iteration. All results and both complete agent versions were persisted
and retrieved through the API. No task containers remained afterward.

| Task | Baseline | Candidate |
|---|---|---|
| fix-git | Failed | Passed |
| regex-log | Failed | Passed |
| git-leak-recovery | Failed | Failed |
| log-summary-date-ranges | Failed | Passed |
| build-cython-ext | Failed | Failed |
| configure-git-webserver | Failed | Failed |
| fix-code-vulnerability | Failed | Failed |
| extract-elf | Failed | Passed |
| nginx-request-logging | Passed | Failed |
| sqlite-db-truncate | Failed | Failed |

Baseline: **1/10**, 9 minutes 13 seconds. Candidate: **4/10**, 7 minutes 27 seconds.
The optimizer proposed stronger command-exit-status checking and error recovery;
the candidate prompt was applied automatically without manual edits.

The candidate was **rejected** because Nginx regressed. Acceptance requires a
strictly higher mean reward and preservation of every previously passing task.
The final retained score is therefore **0.1**, with stop reason `no_improvement`
(no accepted improvement). Although the job allowed two proposals, this rejection
stopped the loop after the first. Higher aggregate performance alone is insufficient
under this policy. Repeated evaluation and a statistically informed acceptance
policy would be useful follow-ups: one trial per task does not establish that the
prompt caused either gains or regressions.

| Usage | Input tokens | Output tokens |
|---|---:|---:|
| Baseline agent calls | 2,325,236 | 26,599 |
| Candidate agent calls | 2,970,167 | 19,924 |
| Optimizer proposal | 45,192 | 335 |

These are reported token counts, not a billing estimate; agent input counts include
conversation history resent on successive steps. The separate single-task smoke
above is additional usage. See [live-results.json](live-results.json) for sanitized
per-task outcomes, model settings, source hashes, proposal, timing and usage.
Full client output is local in ignored `workspace/live-optimization.json`.

These measurements use the documented 300-second agent budget and CPU/memory
overrides. They describe this local subset experiment, not an official leaderboard
score or held-out generalization result.

Reproduction commands:

```sh
python test_client.py --task-ids fix-git --max-iterations 0
python test_client.py
```

The API remains available on localhost:8080. GitHub authentication is configured;
the submission uses a fork because the account lacks direct upstream push access.
Submission: [PR #32](https://github.com/neosigmaai/auto-harness/pull/32).
