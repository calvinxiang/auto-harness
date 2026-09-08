# Reviewing proposals and checking generalization

`service.experiments` is an operator evaluation tool for saved service jobs. It
reads the best persisted source and development failures, creates a proposal, and
evaluates frozen versions using the same Harbor runner and sandbox budgets. It
does not change API job history or automatically promote a version into a job.
Its reports/checkpoints are stored in the private `/artifacts/experiments/<id>`
directory, separate from the production worker's cleanup sweep.

Generate a reviewable proposal from a completed job:

```sh
docker compose -f compose.service.yaml run --rm --no-deps worker python -m service.experiments propose --job-id YOUR_JOB_ID --optimizer-model gpt-5.4-2026-03-05 --reasoning-effort medium
```

The tool verifies that the saved source matches its raw evidence and that the
current agent runtime/budgets match the baseline. It adds deterministic trace
signals: actual model responses, empty responses, repeated commands and counts.
These are observations, not automatic diagnoses: repeating a command can be a
legitimate verification step. Truncated/malformed traces do not yield invented counts.

After reviewing the code and its supporting evidence, evaluate it:

```sh
docker compose -f compose.service.yaml run --rm --no-deps worker python -m service.experiments evaluate --experiment-id YOUR_EXPERIMENT_ID --repetitions 2 --review-reason "Explain the observed failure and the general mechanism being tested"
```

The plan is fixed before execution. It runs two repetitions on the 10 development
tasks, alternating baseline/candidate order, then one paired evaluation on three
held-out tasks. Both versions receive the same model, tasks and execution budgets.
Source hashes and completed runs are checkpointed. Repeating the command resumes
unfinished work; changing the plan or source is rejected. Infrastructure failures
retain evidence and stop evaluation rather than being dropped from the results.

If evidence shows that the agent destroyed its own task environment, an operator
can explicitly assess that task as a zero-score agent failure before resuming:

```sh
docker compose -f compose.service.yaml run --rm --no-deps worker python -m service.experiments assess-agent-failure --experiment-id YOUR_EXPERIMENT_ID --run-id FAILED_RUN_ID --task-id TASK_ID --reason "Describe the concrete agent action and resulting verifier failure"
```

This is allowed only after the experiment has stopped. It retains unmodified raw
results, adds the operator's assessment, awards zero, and resumes without retrying
the damaged task. It cannot award a pass or replace valid verifier results. Report
these assessments alongside normal scores. Unknown infrastructure errors remain
unresolved; the production API continues to fail conservatively on missing rewards.

| Held-out task | Category | Reason selected |
|---|---|---|
| `cancel-async-tasks` | Software engineering, hard | Python concurrency and cancellation behavior |
| `openssl-selfsigned-cert` | Security, medium | Command execution and artifact verification |
| `large-scale-text-editing` | File operations, medium | Exact transformations on larger inputs |

Selection used task metadata from TerminalBench 2.0 registry revision
`69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`, before seeing evaluation results.
These three tasks are not in the API's 10-task development allowlist. Their
instructions, traces and results are never passed to the optimizer by this tool.

An operator can record a concrete review in a proposal's report and use
`propose --review-feedback EXPERIMENT_ID` for a revision. This passes only the
proposal and review, and is refused once held-out evaluation has started.
This review loop is an experimental workflow; ordinary API jobs remain automatic.

The agent model remains `gpt-4.1-mini` for the controlled comparison. Only the
optimizer uses a pinned GPT-5.4 snapshot with medium reasoning. Its API/reasoning
support was checked against [official model documentation](https://developers.openai.com/api/docs/models/gpt-5.4).
`OPTIMIZER_REASONING_EFFORT` is optional and should stay empty for providers/models
that do not support that parameter. Model choice alone is not an improvement claim.

Two development repetitions and three held-out tasks are a small check, not a
statistically conclusive result. Report per-run/per-task outcomes, regressions,
runtime and usage; do not select only favorable trials or tune using held-out results.

Offline contract checks support text completion and a `finish` tool with summary,
verification command and optional output excerpt. The fixture is separate from
the scored runtime; changing its simulated responses does not change either
frozen agent snapshot. Its source hash is saved with each validation result.
This smoke check cannot simulate every possible new tool protocol.
