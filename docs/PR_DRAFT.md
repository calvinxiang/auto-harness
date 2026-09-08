# Proposed title

Add durable multi-tenant agent optimization service

# Proposed description

The original harness requires a coding agent to drive a shared-file optimization
loop. This adds an HTTP service that accepts benchmark jobs immediately, executes
the terminal agent inside disposable Docker task containers, and persists proposed
prompt versions, benchmark evidence and acceptance decisions in PostgreSQL.

- FastAPI with organization membership/roles, ownership checks, idempotent job
  submission, polling, cancellation and full iteration history.
- PostgreSQL queue with SKIP LOCKED claims, renewable leases, fenced checkpoints
  and bounded crash recovery. Separate API and worker; sandbox engine uses mutual TLS.
- Fixed 10-task TerminalBench 2.0 subset, sandbox-installed agent runtime and a
  prompt optimizer that retains strictly improving versions without task regressions.
- End-to-end Python client, versioned SQL migrations, locked dependencies, Compose
  setup, CI and documented tradeoffs. Original CLI workflow remains available.

Validation: 13 PostgreSQL tests pass, including the actual test client over live
HTTP with deterministic execution fixtures. A real Harbor/Docker task smoke passes,
verifying runtime execution, trace capture, verifier integration and denial of Docker
API access without client certificates. The smoke uses a local model fixture and
deliberately leaves the task unsolved.

Live `gpt-4.1-mini` validation completed the 10-task baseline and one automatically
proposed prompt candidate in 16m47s, with zero runner errors. Baseline passed 1/10;
candidate passed 4/10 but regressed on the previously passing Nginx task. The
regression policy correctly rejected the candidate, retained baseline score 0.1
and stopped. Full source/history were retrieved through the client. Measurements,
usage and limitations are recorded in docs/VALIDATION.md and docs/live-results.json.

E2B integration, unrestricted code optimization,
held-out evaluation and production-grade VM isolation are outside this implementation.
