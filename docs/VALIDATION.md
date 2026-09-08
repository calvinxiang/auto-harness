# Validation record

Recorded 2026-09-08 on Windows Docker Desktop using Linux containers.

## Python code optimization extension

The optimizer now changes the complete agent policy module: tool definitions,
dispatch, context handling, helper functions, prompt and the agent loop. The
runtime/model configuration and benchmark remain service-controlled.

- 24 PostgreSQL tests pass, including preservation of invalid code proposals and
  the best version, source diffs, validation results, tool-message pairing, static
  checks that never execute proposed code, and independent trace preservation.
- Offline preflight and a real Harbor task successfully executed a code change
  adding a tool and dispatch branch. Run ID:
  `021c516b-3fde-4cc8-a30d-93909d6ed194`. This used a local HTTP model fixture,
  checked task isolation, and deliberately left the benchmark task unsolved.
- Real offline containers rejected import-time failure and broken tool history
  (exit 1), and terminated an infinite loop (exit 124) under the 15-second timeout.
  Reproduce with `docker compose -f compose.service.yaml run --rm --no-deps worker python -m tests.sandbox_validation`.
- A live 10-task baseline plus one code proposal is running as job
  `4f31b776-a891-4bac-a0c3-3b9f07e14387`. Record measured results after completion.
- An initial deployment attempt (`3734951a-e9b5-470f-a36e-6ae135b5fea6`) stopped
  before inference because the separate migration image was stale. Rebuilt all
  images and confirmed migrations 001 and 002 before resubmitting. This is not a
  benchmark result.

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

## Full live optimization run

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

The API remains available on localhost:8080. Publishing the branch and opening a PR
still require writable GitHub access and working authentication; no PR is open yet.
