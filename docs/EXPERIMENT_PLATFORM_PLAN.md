# Experiment platform: revised priorities

Planning record, 2026-09-08. The initial gap analysis below is historical.
Packages, queued experiments, task-level recovery and global admission are now
implemented; the real worker/container failure drill passed. See
[EXPERIMENT_PLATFORM.md](EXPERIMENT_PLATFORM.md) and [OPERATIONS.md](OPERATIONS.md).
The tools/context/skills comparison and all proposal outcomes are recorded in
[PACKAGE_EXPERIMENT.md](PACKAGE_EXPERIMENT.md). The gap table below describes
the starting point before this implementation, not the current service.

The interviewer clarified that a broad harness search space and solid, scalable
experiment infrastructure matter more than obtaining an immediate score gain.
The desired search space includes tool calls, skills and context management.
The Astra run is baseline calibration; further model/score chasing is not the
next priority. Existing scores and failures remain valid evidence.

## Current implementation and gaps

| Area | Implemented | Gap |
|---|---|---|
| Agent changes | LLM replaces a Python policy with editable tools, dispatch, helpers, context and loop | One module; no versioned skill assets or multi-file harness package |
| Search | One evidence-backed mutation of the best version per iteration | Greedy chain stops at first rejection; no campaign exploring independent hypotheses |
| API jobs | PostgreSQL queue, leases, stale-worker fencing, cancellation, tenant checks and snapshots | A job runs a whole benchmark iteration; interrupted iterations repeat unfinished work at coarse granularity |
| Comparisons | Frozen variants, repeated development runs, held-out runs, saved reports and resume | Operator CLI runs sequentially from JSON, outside durable API scheduling; concurrent invocation lacks ownership fencing |
| Capacity | Per-organization active-job quota, two task containers per worker, isolated sandboxes | No global task-slot admission or demonstrated throughput/load test; artifacts and sandbox engine are local to this deployment |
| Configuration | Execution settings captured with each job | Workers require matching deployment settings; independent profiles cannot be scheduled freely on one pool |

The existing tests establish specific correctness properties. They do not establish
production capacity, multi-host portability or safe concurrent experiment control.

## Next implementation slices

1. **Version the harness as a package.** Store a manifest, entry point, Python
   modules, tool definitions, reusable skill instructions/scripts and context
   configuration as an immutable version, with parent lineage and file hashes.
   Validate paths and contents before sandbox execution. Preserve old single-module
   history. A skill here is a reusable procedure and its resources, with explicit
   discovery/loading behavior; benchmark-specific answers are not skills.
2. **Make experiments durable API resources.** Persist the experiment plan,
   candidate versions, development/evaluation splits, repetitions and execution
   profile. Schedule candidate/task/repetition trials through PostgreSQL-backed
   workers with leases and fenced completion. Aggregate results only after the
   required trials terminate. Resume skips completed trials; lost work may repeat,
   while only the current owner can commit a result. Cancellation covers children.
   Enforce tenant visibility on experiments, versions, trials and artifacts.
3. **Demonstrate several distinct mutations.** Generate and evaluate a tool-design
   change, a context-management change and a reusable-skill change. Save each
   hypothesis, changed files, parent version, validation and outcome. Exploration
   continues within a frozen candidate budget even when one candidate is worse;
   selection/promotion stays separate. Avoid changing many dimensions together.
4. **Exercise operational failure and capacity.** Run multiple workers and queued
   experiments under an explicit sandbox-slot limit. Kill a worker during a trial,
   cancel an experiment, interrupt a sandbox, and submit duplicate requests. Verify
   lease recovery, rejection of stale writes, preservation of completed trials,
   bounded concurrency, tenant isolation and cleanup. Report measured throughput,
   queue wait and completion latency for the actual tested machine and workload.

## Boundaries and evaluation

The mutable search space is the agent harness: tools and their invocation,
orchestration, skills, memory/context policies, verification and recovery behavior.
The fixed experiment controller enforces authentication, sandbox isolation,
credential handling, resource ceilings, benchmark integrity and result persistence.
This boundary makes arbitrary proposed agent code reviewable and repeatable.

Model and resource settings belong in the experiment profile. Hold them fixed for
a harness comparison; model/budget sweeps can be separately labeled experiments.
Report per-task outcomes, failures, runtime, model calls and usage, even when a
candidate does not improve. More tools, broader search and 7/10 on one model are
not themselves evidence of improvement or scalability.

For this take-home, prove the worker/queue behavior on the existing Docker setup
before adding another queue product or a cluster deployment. The artifact-store
and sandbox-provider boundaries can support a later multi-host deployment; the
current shared-volume/Docker-engine implementation should remain explicitly local.
