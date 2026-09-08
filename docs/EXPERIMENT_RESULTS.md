# Controlled agent-code comparison

Completed on 2026-09-08: all **46 task executions**, across six runs, in **46m27s**
including the pause for the error assessment below. The candidate showed a small
development gain and failed to improve held-out results. It is not recommended for
promotion. No existing API job history or best version was changed.

| Evaluation | Baseline | Candidate |
|---|---:|---:|
| Development repetition 1 | 1/10 | 1/10 |
| Development repetition 2 | 1/10 | 3/10 |
| Development total | 2/20 (10%) | 4/20 (20%) |
| Held-out tasks | 1/3 | 0/3 |

Each development pair had a regression: Git leak recovery in the first, date-range
log summaries in the second. Held-out async cancellation also regressed. These
small samples do not establish a reliable gain or quantify generalization, and
the concrete completion-tool defect below remains a reason not to promote it.

Full generated module, diff, source hashes, configuration, run IDs, per-task
outcomes, assessments and usage: [code-experiment-results.json](code-experiment-results.json).

## Question and evidence

Does requiring an explicit, checked completion tool improve the terminal agent?
The source is the best saved version from API job
`4f31b776-a891-4bac-a0c3-3b9f07e14387`, whose historical baseline passed 4/10.
That historical score is not counted as a repetition in this experiment.

The recorded development failures included completion after an empty regex result,
failed service commands without a subsequent HTTP check, and a source edit after
pytest failed without rerunning pytest. These support testing better completion
verification; they do not establish one cause for every failed task. By contrast,
the previous empty-response hypothesis had no examples in 352 baseline assistant
messages. Deterministic trace counts now make that evidence available to proposals.

## Proposal and review

The tested proposal is an LLM-generated Python module, with no manual source edits.
It adds a `finish` tool and dispatcher that checks a cited command against actual
bash history, rejects tested failed/stale/missing checks, and requests further work
when completion is unsupported. Silent shell tests and Python assertions passed
offline review probes. It accepts arbitrary verification commands rather than
restricting them to a named test-runner whitelist.

This remains a heuristic. A command can succeed while testing the wrong property.
Write detection is incomplete, command matching permits approximate matches, and
the global 80-call limit is the bound on repeated completion rejections. The task
verifier, rather than the finish tool, determines benchmark success.

Proposal history before freezing the candidate:

| Experiment | Outcome before benchmarking |
|---|---|
| `54225391-049e-4381-bd1f-f05c26557632` | A new 4.1-mini proposal repeated the unsupported empty-response hypothesis; not evaluated |
| `0ade3a03-d692-471b-8ef0-ac50019175ff` | GPT-5.4 proposed completion verification, but offline probes showed its whitelist rejected valid silent checks; revision requested |
| `1bb0c3ba-75d0-4719-a050-27932a5a549c` | Revision response failed validation before execution; exact cause and usage were not retained |
| `bb881cf2-6f45-4ff3-9149-1031c0e2274f` | Retry produced the explicit finish tool; static, sandbox and targeted review checks passed; candidate frozen for evaluation |

The retry allowed a larger completion budget and improved safe error diagnostics.
The earlier validation failure is not established to have been a token-limit error.
The first successful GPT-5.4 proposal used 28,240 input / 11,089 completion tokens;
the final proposal used 31,383 input / 8,955 completion tokens. Completion includes
reasoning tokens. These are reported usage counts, not a billing estimate.

This is an operator-reviewed experiment, including one requested revision. It is
separate from ordinary automatic API jobs and does not modify their iteration
history, acceptance policy or best version. No held-out evidence entered a proposal.

## Frozen comparison

The agent model is `gpt-4.1-mini` in both versions. Only the proposal optimizer uses
`gpt-5.4-2026-03-05` with medium reasoning. Both frozen agents use the same runtime,
300-second task limit, 80-call budget, 120-second bash timeout, task revision,
1 CPU / 2 GB task containers and concurrency of two.

The plan is baseline then candidate on all 10 development tasks, candidate then
baseline for the second repetition, then one baseline/candidate pair on three
held-out tasks: **46 task executions**. The held-out selection and rationale are
in [EXPERIMENTS.md](EXPERIMENTS.md). Their metadata was selected before any results;
their instructions and outcomes are excluded from optimizer feedback.

The offline smoke fixture was extended to simulate a finish-tool response. It is
separate from the scored runtime, so neither frozen agent snapshot changed. Both
text and tool completion passed, and crashing code, invalid tool history and an
infinite loop remained rejected. Validation saves the fixture's source hash.

## Error assessment during the first candidate repetition

On `configure-git-webserver`, the agent generated a post-receive script with an
unquoted heredoc. Variables expanded while the script was being written; its
cleanup command subsequently deleted files across the task container rather than
only the intended web directory. The trace records removal errors under system
paths, followed by missing `/tmp` and command failures. Harbor then reported
`RewardFileNotFoundError` with empty verifier output because verification could
not run. The API and other task containers continued normally.

This is evidence of agent-caused damage, not an infrastructure glitch to retry
away. The raw error is retained, with a separate operator assessment assigning
zero reward and resuming the same frozen plan without rerunning that task. This
manual classification was added after the unexpected failure, before held-out
evaluation; it must be distinguished from a normal verifier-issued zero.

It also exposes a weakness of the generated finish tool: bash exceptions were
reported to the model but not added to its completion-check event list. Eventually
it accepted an earlier zero-exit git push despite error text and later failures.
Neither the candidate nor its runtime was changed during the comparison.

Two development repetitions and three held-out tasks can reveal obvious regressions
and sampling variation, but cannot establish a reliable general improvement.

## Per-task outcomes

Development cells count passes across two attempts for each version.

| Task | Baseline | Candidate |
|---|---:|---:|
| fix-git | 0/2 | 1/2 |
| regex-log | 0/2 | 0/2 |
| git-leak-recovery | 1/2 | 0/2 |
| log-summary-date-ranges | 1/2 | 1/2 |
| build-cython-ext | 0/2 | 0/2 |
| configure-git-webserver | 0/2 | 1/2* |
| fix-code-vulnerability | 0/2 | 0/2 |
| extract-elf | 0/2 | 0/2 |
| nginx-request-logging | 0/2 | 1/2 |
| sqlite-db-truncate | 0/2 | 0/2 |

*The other candidate attempt is the explicitly assessed agent-damage failure,
not a verifier-issued zero. Its original missing-reward error is retained.

| Held-out task | Baseline | Candidate |
|---|---|---|
| cancel-async-tasks | Passed | Failed |
| openssl-selfsigned-cert | Failed | Failed |
| large-scale-text-editing | Failed | Failed |

## Timing and usage

| Run | Duration | Input tokens | Output tokens | Model calls |
|---|---:|---:|---:|---:|
| Development baseline 1 | 8m01s | 2,679,191 | 16,419 | 372 |
| Development candidate 1 | 9m13s | 3,191,719 | 30,831 | 461 |
| Development candidate 2 | 7m47s | 3,315,579 | 18,215 | 361 |
| Development baseline 2 | 7m46s | 2,768,870 | 19,580 | 424 |
| Held-out baseline | 5m56s | 460,263 | 8,355 | 77 |
| Held-out candidate | 6m26s | 349,861 | 5,610 | 67 |

Counts include the assessed failure and repeated conversation input. Proposal
usage is separate above. No benchmark task was retried or omitted. There was one
raw missing-reward error, assessed as agent damage; the other 45 executions
produced verifier results. All task runs used registry commit
`69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`. No benchmark containers remained afterward.
