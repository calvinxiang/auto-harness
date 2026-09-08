# Original agent with a flagship model

Completed on 2026-09-08: **7/10 tasks passed**, 3 failed, zero runner errors, in
**13m01s** (22:25:45 to 22:38:46 UTC). API job
`8ef90c0f-134d-4009-918c-0dcc64a92418` succeeded with one baseline iteration and
zero optimizer proposals. Full configuration, frozen runnable source, hashes,
validation, usage and per-task results are in [flagship-results.json](flagship-results.json).

| Original policy evaluation | Result |
|---|---:|
| Earlier mini baseline in the automatic code-optimization job | 4/10 |
| Recent mini development repetitions | 1/10 and 1/10 |
| Astra with extra-high reasoning, this run | 7/10 |

The Astra configuration outperformed those historical totals. It did not solve
all tasks or improve every task: Nginx logging passed in the earlier 4/10 mini
baseline and failed here. There were no manual score adjustments or retries.

| Task | Result |
|---|---|
| `fix-git` | Pass |
| `regex-log` | Pass |
| `git-leak-recovery` | Pass |
| `log-summary-date-ranges` | Pass |
| `build-cython-ext` | Pass |
| `configure-git-webserver` | Fail |
| `fix-code-vulnerability` | Pass |
| `extract-elf` | Fail |
| `nginx-request-logging` | Fail |
| `sqlite-db-truncate` | Pass |

Recorded usage: 989,676 input tokens, 47,617 output tokens (including 16,080
reasoning tokens), and 89 model calls across all 10 tasks. These are saved response
usage counts, not a billing estimate or a guarantee that interrupted requests are
fully accounted for.

The question is whether a stronger model can solve the development tasks without
optimizing the agent policy. The configured key listed `gpt-6-astra`, and live
single-call and tool-result round-trip probes succeeded before the benchmark.

## Configuration

| Setting | Value |
|---|---|
| Agent model | `gpt-6-astra` (alias; no dated Astra snapshot listed by the key) |
| Agent policy | Original baseline, no optimizer proposal |
| Model API | Responses |
| Reasoning effort | `xhigh` |
| Output allowance per request | 32,768 tokens, including reasoning |
| Tasks | Existing 10-task development subset |
| Agent time / calls | 300 seconds / 80 calls per task |
| Shell command timeout | 120 seconds |
| Concurrency / resources | 2 tasks / 1 CPU and 2 GB per task |
| Dataset / runner | TerminalBench 2.0 / Harbor 0.1.45 |

The policy, prompt and tool definitions are held fixed. Model, API transport and
reasoning/output allowance change together, so this is a model/configuration
comparison rather than a strict model-only ablation. Historical mini scores are
not a concurrent control. This is one development repetition, with no held-out
evaluation or claim of an optimizer gain.

[OpenAI's model guidance](https://developers.openai.com/api/docs/guides/latest-model)
requires Responses for Astra tool calls.
[Reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning)
recommends sufficient space for reasoning and final output. The adapter preserves
native reasoning items and assistant phases while exposing the same synchronous
function-tool interface to the policy. It detects incomplete output before a
partial tool call could execute. No new solver tools or manual policy edits were added.

The job retains its complete runnable source and execution configuration. A later
logging-only fix deep-copies recorded provider requests so future policy edits
cannot rewrite the evidence; it was tested separately and did not change this
running job's frozen source.

## Failure analysis: Git webserver

The agent's local clone/push/HTTP check succeeded. It then removed its temporary
test data and left an empty repository ready for the requested first push. The
verifier returned zero after its SSH clone/push flow failed and the HTTP request
returned 404. The agent completed normally in seven model calls, without a model
output limit or agent timeout.

There is a specification wrinkle: the instruction uses `user@server` and delegates
login setup to the user; the verifier uses a `git@localhost` password-login flow.
The agent documented that it had not configured SSH authentication. This evidence
does not prove that the local deployment hook was broken or that a larger model
would fix the mismatch. The official zero is retained, without editing the task,
changing the reward, or rerunning it with verifier-specific instructions.

## Failure analysis: binary extraction

`extract-elf` received zero: the output matched 0% of reference addresses where
the verifier requires at least 75%. Harbor recorded 305.3 seconds of agent execution,
consistent with the 300-second watchdog plus its five-second kill grace. The saved
trace ends with tool work rather than a final completion message. Its last metadata
snapshot still contains the default `agent_declared_complete` label; that snapshot
is not evidence of a clean completion. The adapter did not separately persist the
watchdog's exit code, so the timeout attribution is an inference from timing and
the unfinished trace. The valid verifier failure is retained. This run cannot
establish whether the task would pass with a longer execution allowance.

## Failure analysis: Nginx logging

Seven of eight verifier checks passed, including the running server, content,
configuration, custom 404 and log creation. The final format check required the
quoted user-agent field at the end of the line. Astra appended an extra
`request_time` field after it, so the task received zero. The instructions require
the quoted user agent but do not explicitly require it to be the final field.
The agent's own HTTP, rate-limit and log-presence checks all passed; they did not
check that exact ordering. This illustrates the difference between useful local
verification and the benchmark's exact acceptance condition. The score is unchanged.

## Reproduce

Set the four agent settings shown in README, recreate the API and worker, then:

```sh
python test_client.py --max-iterations 0
```

This uses the normal asynchronous API, durable job history and sandbox runner.
The service and adapter changes pass 37 PostgreSQL tests, including native output
replay, complete tool-result groups, context edits, incomplete-output handling and
fixed comparison settings. Positive and negative offline sandbox checks also passed.
