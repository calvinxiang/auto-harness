# Automatic harness search: live validation

Run `caab9717-1377-4def-afeb-0a23bdc357f0` completed **24 real task executions and
six proposals across two automatic rounds in 60m54s**. The controller selected a
control-flow candidate at 1/2 against a fresh baseline at 0/2, then retained it
when every second-round candidate tied. The final confirmation returned 0/2 for
both versions; both passed the single designated held-out task. **The apparent
development gain did not reproduce, and no reliable improvement is claimed.**

The [complete JSON report](automatic-optimization-results.json) contains all
seven exact packages, proposals, validation results, task iteration histories,
source hashes, recorded mechanisms and independent decision/split audits.

## Frozen protocol

The development tasks are `configure-git-webserver` and `extract-elf`, selected
from failures in the earlier ten-task Astra baseline. Each version runs once per
task. The initial package is the same frozen baseline used in the earlier
[package comparison](PACKAGE_EXPERIMENT.md).

Each round proposes independent changes to context, skills and control flow.
The controller validates their packages, evaluates a fresh incumbent alongside
valid candidates, and selects a strictly higher development score only when no
paired passing task regresses. It can run at most two rounds and stops after two
rounds without an eligible improvement. A perfect development score stops early.
No candidate source is manually revised during this run, and no scored task is
manually retried.

After selection freezes, the initial and selected versions receive a final
development confirmation and the designated held-out task,
`large-scale-text-editing`. The confirmation is required by the existing
experiment contract; its additional executions are included in the maximum of
24 trials. Neither final split can change selection or generate another proposal.
The held-out task appeared in earlier project experiments, so it is not newly
unseen data.

| Setting | Value |
|---|---|
| Agent | GPT-6 Astra, Responses, `xhigh`, 32,768 output-token allowance |
| Optimizer | GPT-5.4, Chat Completions, `medium` |
| Task budget | 300 seconds, 80 model calls, 1 CPU, 2 GB |
| Workers / admitted task slots | 2 / 2 |
| Maximum proposals / scored trials | 6 / 24 |
| Controller code used for this run | `46e7d4c` |

To repeat the protocol after setup, set `OPTIMIZER_MODEL=gpt-5.4` and
`OPTIMIZER_REASONING_EFFORT=medium` in `.env`, recreate the services, and use a
new client state file:

```sh
python test_client.py --optimize --state workspace/repeat-search-state.json --output workspace/repeat-search-results.json --task-ids configure-git-webserver extract-elf --heldout-task-ids large-scale-text-editing --dimensions context skills control --max-rounds 2 --patience 2 --model gpt-6-astra --api responses --reasoning-effort xhigh --max-output-tokens 32768
```

This repeats the plan and settings; new model generations can produce different
packages and outcomes. The saved report records the exact versions from this run.

See [the workflow](AUTOMATIC_OPTIMIZATION.md) for API details, transactional
recovery, limits and the exact selection policy. Controller failure isolation at
`c14b329` is covered by the **67-test suite** and was deployed after this run.
The API and both workers were checked against the tested source. Resuming the
client after deployment returned an identical complete report; no active jobs,
task reservations or task containers remained at the deployment boundary.

## Search decisions

The controller selected the first control candidate, then retained it when every
second-round candidate tied its fresh score. Search stopped at `max_rounds`.

| Evaluation | Version | Development passes |
|---|---|---|
| Initial evidence | Original baseline | 0/2 |
| Round 1 | Fresh original baseline | 0/2 |
| Round 1 | Context candidate | 0/2 |
| Round 1 | Skills candidate | 0/2 |
| Round 1 | Control candidate, selected | 1/2 |
| Round 2 | Fresh incumbent (round 1 control) | 1/2 |
| Round 2 | Context added to incumbent | 1/2 |
| Round 2 | Skills added to incumbent | 1/2 |
| Round 2 | Completion audit added to incumbent | 1/2 |
| Final confirmation | Original baseline | 0/2 |
| Final confirmation | Selected round 1 control | 0/2 |

Final held-out results: original baseline **1/1**, selected control **1/1** on
`large-scale-text-editing`. This previously used project split provides one more
check, not evidence of generalization to unseen tasks.

The passing task was `configure-git-webserver`; every scored development
extraction trial in these two rounds failed. The second-round ties did not cause
a replacement. An independent export audit recomputed both decisions from the
individual task rewards and checked parent hashes, frozen profiles, prior-round
context and permitted evidence job IDs.

The second control package's new final-audit message appeared in neither live
trial's saved requests. Both executions ended with supervisor code 137 while
the inherited verification loop was active, although the webserver task still
earned a passing reward. Its offline contract completed in three fixture model
calls. Thus the added audit exists in the source and passes the fixture, but
these live results do not measure its intended final-audit behavior.

## Candidate review and limits

The final development confirmation returned **0/2 for both the original baseline
and selected control package**. The control package's earlier webserver success
did not repeat in this confirmation. Its frozen selection score remains 1/2;
the separate final score is 0/2. This is not a reliable improvement and does not
support promoting the candidate. No scored rerun was added after that outcome.

The first context proposal adds working-memory summaries and a recent-message
window. It preserves the separately logged full trace. The implementation also
summarizes recent outputs before eviction, which can duplicate information, and
places tool-derived summary text into a system message. These are properties of
the generated proposal, not established improvements to relevance or trust.

The first skill proposal discovers and loads a reusable verification checklist
through the package asset API. Loading a skill establishes that the mechanism
ran; it does not establish that the model followed every instruction.

The first control proposal tries to delay completion after effectful commands
until a subsequent observational command. A read-only audit of its regex literals
found excess escaping: ordinary `mkdir`, `rm`, `git init` and `pip install`
commands do not match their intended detection patterns, while output redirection
does. Unclassified commands count as observational, and successful exit status
is not required to clear the check. Structural/offline contract validation did
not catch those semantic limitations. No manual fix was applied to its scored
source.

The second round starts from the first control package selected by the server.
Its context proposal adds bounded working notes and a ten-message tail target
that preserves complete tool-call groups. Its skill proposal loads an
outcome-verification checklist; its control proposal
adds a final completion audit before accepting a no-tool response. These are
compositions with the selected parent's behavior, not new changes to the original
baseline. All three retain the first control gate, including the limitations
above. Their optimizer inputs include that parent's development evidence and
the first round's persisted decisions.

Both initial baseline agents performed their own checks and declared completion,
but the verifier rejected their outputs. The webserver report shows HTTP 404;
the binary extraction report shows zero reference coverage. These observations
do not establish that premature completion or context loss caused either failure.
They show why optimizer diagnoses need to be treated as hypotheses and why
self-authored checks are not equivalent to benchmark success.

A later read-only review of the cached development verifier also confirmed that
binary extraction is checked against a newly compiled input. The baseline's
recorded independent-loader self-check used the supplied binary. This is an
observable difference in test coverage, not a proven explanation of every
extraction failure. That review was not fed back into this run's proposals or
used to alter its task scores.

This is a small diagnostic run with one repetition, not evidence of statistical
significance or an improvement across the full benchmark. It also measures one
Docker host, not multi-host scalability. The useful infrastructure evidence is
the persisted chain from proposal to validated version, task attempts, comparison,
selection and stopping, alongside the separate recovery tests and failure drill.

## Interpreting interrupted executions

Some trials have supervisor exit code 137 and stale agent metadata whose status
is still `running`. The recorded first-round extraction durations are about
305 seconds: consistent with the launcher's 300-second `timeout` followed by its
five-second kill escalation. This timing supports deadline termination as an
inference; exit 137 alone does not establish which process sent SIGKILL.

The existing `watchdog_timeout` field means that GNU timeout returned 124. A false
value does not rule out escalation to 137. This report therefore counts 124 and
137 separately and retains the raw metadata. A later valid verifier result can
still produce a task reward after interruption. Such a result is not evidence
that the agent finished normally, and no interrupted attempt is silently retried.

## Recorded execution and mechanism evidence

All 24 trials ran once and produced verifier rewards. Supervisor exits were
**14 normal, one timeout (124), and nine killed (137)**. Agent metadata reported
14 normal completions, one error and nine stale `running` states. These interrupted
executions are retained as observed, with their verifier results. A zero error
count in an experiment summary does not mean that every agent exited normally.

| Mechanism | Evidence from actual requests / asset metadata |
|---|---|
| Round 1 context | Memory summaries in 22 requests; 11 requests omitted older tool results. |
| Round 2 context | Memory summaries in 35 requests; 34 omitted older tool results, up to 19 at once. |
| Skills | Skill discovery/loading in all four trials of the two skill packages. |
| First control gate | Reminder messages appeared in four of the selected package's seven trials, and in both trials of each second-round descendant. |
| Second control audit | Its new audit message was absent from both live trials; inherited verification reminders were present. |

These counts establish which mechanisms executed, not their causal contribution
to reward. Every trial resolved to `gpt-6-astra` and recorded dataset revision
`69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`; per-task checksums are in the JSON.
Recorded agent usage was 371 model calls, 4,209,022 input tokens and 219,844 output
tokens. The six optimizer generations recorded 47,259 input and 25,172 output
tokens. Logged usage may be incomplete for interrupted calls and is not a billing
reconciliation.

The export verifies package/file/runnable hashes and embedded assets against the
saved versions. It independently recomputes both selections from individual task
rewards and checks proposal parents, prior-round context, frozen profiles and
development-only evidence IDs. The final experiment was created after selection;
its last development trial finished at `01:12:38.651124 UTC`, before the first
held-out trial started at `01:12:38.704551 UTC` on 2026-09-09.
