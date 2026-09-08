# Proposed title

Add durable multi-tenant agent optimization service

# Proposed description

The original harness requires a coding agent to drive a shared-file optimization
loop. This adds an HTTP service that accepts benchmark jobs immediately, executes
the terminal agent inside disposable Docker task containers, and persists proposed
Python agent versions, code diffs, validation outcomes, benchmark evidence and
acceptance decisions in PostgreSQL.

- FastAPI with organization membership/roles, ownership checks, idempotent job
  submission, polling, cancellation and full iteration history.
- PostgreSQL queue with SKIP LOCKED claims, renewable leases, fenced checkpoints
  and bounded crash recovery. Separate API and worker; sandbox engine uses mutual TLS.
- Fixed 10-task TerminalBench 2.0 subset, sandbox-installed agent runtime and a
  code optimizer for tools, context management and agent control flow. Proposed
  modules pass static checks and an offline sandbox contract check before evaluation.
  Only strictly improving versions without task regressions are retained.
- End-to-end Python client, versioned SQL migrations, locked dependencies, Compose
  setup, CI and documented tradeoffs. Original CLI workflow remains available.
- Operator evaluation tool for reviewed proposals, repeated comparisons and a
  separate held-out set, with frozen source hashes and resumable run checkpoints.

Validation: 32 PostgreSQL tests pass, including the actual test client over live
HTTP with deterministic execution fixtures. A real Harbor/Docker task smoke passes,
verifying runtime execution, trace capture, verifier integration and denial of Docker
API access without client certificates. The smoke executes an added tool and its
dispatch using a local model fixture, deliberately leaving the task unsolved.
Separate offline containers reject crashing code, invalid tool history and an
infinite loop within the configured timeout.

Live `gpt-4.1-mini` code optimization ran a 10-task baseline and an automatically
proposed Python change in 17m12s with zero runner errors. Baseline passed 4/10;
candidate passed 1/10, was rejected, and baseline score 0.4 was retained. Full
code/diff/validation/history were retrieved through the client. The proposed
empty-response diagnosis was unsupported by baseline traces; optimizer inputs now
include runtime metadata, trace observations and passing-task context.

A separate operator-reviewed GPT-5.4 proposal added a completion-verification tool.
With both benchmark agents fixed to `gpt-4.1-mini`, 46 task executions compared
two development repetitions (baseline 2/20, candidate 4/20) and three held-out tasks
(baseline 1/3, candidate 0/3). Each development pair had a regression. One candidate
damaged its own task filesystem; its missing verifier reward is preserved and
explicitly assessed as a zero-score failure without retry. The candidate is not
recommended for promotion. All outcomes, generated source, hashes, review decisions
and usage are in docs/EXPERIMENT_RESULTS.md and docs/code-experiment-results.json.
This reviewed experiment is separate from the automatic API loop and does not
change an existing job's history or best version. No reliable general gain is claimed.

E2B integration, optimization of service/runtime infrastructure,
statistical promotion rules and production-grade VM isolation are outside this implementation.

Review links: [setup and design](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/README.md),
[experiment report](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/docs/EXPERIMENT_RESULTS.md),
[generated code and structured results](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/docs/code-experiment-results.json),
[validation record](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/docs/VALIDATION.md).
