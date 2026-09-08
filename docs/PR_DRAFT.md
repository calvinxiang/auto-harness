# Proposed title

Add durable harness experiments and multi-tenant optimization service

# Proposed description

The original harness needs a coding agent to drive a shared-file loop. This adds a FastAPI/PostgreSQL service that queues sandboxed TerminalBench work, proposes versioned harness changes, and preserves the full history. Experiments now compare multi-file packages with independent tools, context and skill hypotheses; a worker crash retries an interrupted task while keeping completed trials.

- Immutable packages contain Python modules, skill procedures/resources and configuration, with parent lineage, file diffs, hashes and exact runnable source. Proposed code loads only inside Docker sandboxes.
- Durable experiments freeze candidate versions, model/budget profiles, development/held-out splits and repetitions. Each task trial uses leased, fenced PostgreSQL jobs. Cancellation covers unfinished children; tenant/owner checks cover versions, experiments, jobs and evidence.
- A shared admission pool bounds work across workers and retains reservations until cleanup. A separate lease watchdog stops execution during blocked heartbeats. The task supervisor records exits/timeouts independently of agent metadata.
- Asynchronous package proposals retain validation and failure evidence. Held-out evidence is rejected as proposal input. Exploration evaluates the frozen candidate set; promotion remains separate. The original iterative optimization endpoint retains strict improvement/no-regression acceptance.
- Compose setup, migrations, locked dependencies, CI, the resumable `test_client.py --experiment` workflow, and operator inspection of actual context/skill/tool use are documented. The original CLI is preserved.

Validation: **57 PostgreSQL tests pass**, including both client workflows over real HTTP, experiment resume/idempotency, cross-tenant access, held-out gating, proposal checkpoints, fencing and capacity. Offline Docker checks load package helpers/skills and reject crashing code, broken tool history and an infinite loop. The real worker failure drill used four processes with two slots: killed worker recovered, stale write rejected, completed history preserved, cancellation and task interruption handled, zero remaining containers/reservations. Peak observed concurrency was two. Its 33.61-second synthetic workload is infrastructure evidence, not production benchmark throughput.

The controlled package comparison is currently running: three development tasks repeated twice and three designated held-out tasks once, across baseline and tools/context/skills versions (36 trials). GPT-5.4 generated the packages; all trial agents use the same Astra Responses profile. Initial rejected proposals are retained. Logs already confirm structured command results, tool-output compaction and declared skill loading. Final scores will be added after all trials finish.

Earlier live evidence includes a full ten-task automatic code optimization that rejected a regressing candidate, a repeated 46-trial comparison whose development gain did not carry over to held-out tasks, and the unchanged-policy Astra baseline at 7/10. These historical reports remain available; no general performance improvement is claimed.

Scope limits: one shared Docker engine/artifact volume, no multi-host placement or HA, scoped-key inference proxy, statistical promotion, or production VM isolation. E2B is not implemented. The historical operator comparison CLI bypasses managed admission and should not run concurrently with worker experiments.

Review: [setup](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/README.md), [package and experiment API](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/docs/EXPERIMENT_PLATFORM.md), [operational proof](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/docs/OPERATIONS.md), [prior comparison](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/docs/EXPERIMENT_RESULTS.md), [Astra baseline](https://github.com/calvinxiang/auto-harness/blob/feat/agent-optimization-service/docs/FLAGSHIP_EXPERIMENT.md).
