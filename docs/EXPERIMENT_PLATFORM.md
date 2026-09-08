# Durable harness experiments

An experiment compares immutable harness packages through the same PostgreSQL
queue as ordinary API jobs. It freezes candidates, model settings, tasks and
repetitions before execution. Every candidate/task/repetition is a separate job,
so recovery repeats only an interrupted trial. Completed results remain intact.
The recorded baseline/tools/context/skills comparison is in
[PACKAGE_EXPERIMENT.md](PACKAGE_EXPERIMENT.md).

## Run the complete workflow

Configure agent and optimizer models in `.env`, then start two workers:

```sh
docker compose -f compose.service.yaml up --build -d --scale worker=2
python test_client.py --experiment --state workspace/comparison.json --output workspace/comparison-results.json
```

This creates a baseline package, queues independent **tools**, **context**, and
**skills** proposals, waits for their offline sandbox validation, then evaluates
all four versions on the ten development tasks twice. It is 80 task executions.
For a shorter explicit comparison, add `--task-ids fix-git log-summary-date-ranges
nginx-request-logging`. `--repetitions` accepts 1–3. Held-out evaluation is opt-in:
`--heldout-task-ids cancel-async-tasks openssl-selfsigned-cert large-scale-text-editing`.
Held-out tasks run once per version after all development trials terminate.

`--evidence-job-ids` supplies completed development jobs visible to the caller;
without evidence the optimizer must describe exploratory hypotheses. `--prepare-only`
stops after saving/validating all proposals. Resume using the same `--state` path;
the saved plan and IDs take precedence over new plan flags. Proposal/experiment
requests use stable idempotency keys. Polling timeout leaves server work running.
Failed proposals are retained and stop the client before benchmark submission.
Use a new state file for a new experiment. Credentials remain in ignored local
client files; the result file contains versions, proposals, outcomes and full
iteration history, without authentication tokens.

`--review-feedback` adds bounded feedback to a new proposal set; it is persisted
with the proposal job. Malformed provider output is retained in error details
(bounded and credential-redacted), alongside usage and completion status. Failed
attempts are not silently regenerated.

The model profile may also be supplied with `--model`, `--api`, `--reasoning-effort`
and `--max-output-tokens`. It is identical for every version in an experiment.
Changing workers' model defaults does not change queued trial profiles.

## API resources

All paths are under `/organizations/{org}` and require a bearer token. An admin
sees organization resources; a member sees only resources they own. Inaccessible
versions, experiments, jobs and history return 404. Proposal evidence and parent
versions use the same checks. Raw artifact volumes have no public HTTP route.

| Method and path | Result |
|---|---|
| `POST /harness-versions` | Immutable package, parent, file hashes/diffs and frozen runnable source; 201 |
| `GET /harness-versions[/{id}]` | Bounded list or full package/source |
| `POST /harness-versions/{id}/proposals` | Queued proposal job; 202; dimension and optional evidence job IDs |
| `POST /experiments` | Atomically persisted plan and queued task trials; 202 |
| `GET /experiments[/{id}]` | Bounded list or status, summaries, paired regressions and linked trial jobs |
| `POST /experiments/{id}/cancel` | Cancel unfinished children; preserve completed results |
| `GET /jobs/{id}[/iterations]` | Proposal output or full trial history and bounded artifact excerpts |

Proposal and experiment submissions accept `Idempotency-Key`; a changed request
with the same key returns 409. An organization can have three active experiments
and ten active non-trial jobs. Each experiment allows 1–4 versions, 1–10 development
tasks, up to three held-out tasks and 1–3 development repetitions (maximum 132 trials).
Unknown task IDs and invalid limits return 422. API schemas are in `/openapi.json`.

Example experiment body (replace UUIDs with saved version IDs):

```json
{
  "name": "tools-versus-baseline",
  "version_ids": ["BASELINE_UUID", "TOOLS_UUID"],
  "development_task_ids": ["fix-git", "log-summary-date-ranges"],
  "heldout_task_ids": ["cancel-async-tasks"],
  "repetitions": 2,
  "profile": {
    "agent_model": "gpt-6-astra", "agent_api": "responses",
    "agent_reasoning_effort": "xhigh", "agent_max_output_tokens": 32768,
    "agent_timeout_seconds": 300, "max_steps": 80
  }
}
```

## Mutable package and fixed controller

A package is `{entrypoint, files, skills}`. `files` maps relative paths to complete
UTF-8 contents, at most 32 files / 256,000 bytes, with `.py`, `.md`, `.json`, `.sh`
or `.txt` extensions. The entry point defines `run_agent(api, instruction)`;
Python helpers import from the package root. JSON assets can define context/tool
configuration. Skills declare a unique name, description, Markdown path and
resource paths. All referenced files must exist in the package.

The mutable space includes tool schemas and dispatch, multi-step orchestration,
context selection/compaction, recovery logic, skill discovery/loading and reusable
skill procedures/scripts. The controller fixes authentication, result evaluation,
Docker resources, transport, dataset and execution deadlines. The API and worker
compile Python as a syntax check; they never import proposed code. Only the
sandbox extracts and loads a package. The service captures a package hash,
per-file hashes, a runtime hash, runnable-source hash, parent and file diffs.
Comparison versions must share a runtime hash. Create a new baseline after changing
the runtime; retries of already saved versions use their original frozen source.
The iteration `prompt` column belongs to the legacy single-module workflow; package
source and saved model requests are authoritative for dynamically assembled prompts.

The sandbox API exposes `model(messages, tools)`, `bash(command)` and
`tool_result(call_id, content)`, plus `skills()`, `read_asset(path)` and
`asset_path(path)`. Invoke script resources through `api.bash`, quoting their
resolved path. Asset discovery/reads and model-request sizes are recorded in agent
metadata; complete actual requests and a separate event trace remain in artifacts.
Reading a skill is evidence of loading, not proof the model followed it correctly.

Operators can inspect actual compaction and asset loading without executing source:

```sh
docker compose -f compose.service.yaml exec worker python -m service.inspect_experiment EXPERIMENT_UUID
```

The inspector compares model-facing tool messages with independently recorded tool
results. Its output contains counts, character lengths and asset names, not raw
request bodies. It reads the deployment's private artifact volume and database.

Path validation rejects traversal, reserved names and collisions. Static checks
reject obvious benchmark identifiers and protected paths. Package imports are not
restricted to a fixed function/import allowlist. Dependencies must be available in
the task image. The offline container tests imports, a bash interaction, valid tool
pairing and completion. It has no network, credentials or task data, 128 MB memory
and a 15-second deadline. It does not exercise every custom tool or prove arbitrary
Python secure. The agent can access its inference key; budget accounting lives in
the runtime and is not a hostile-code security boundary. External sandbox timeout
and resource limits remain enforced by the controller.

## Scheduling, failure and selection

A short transaction locks the shared execution-pool row, admits work under its
capacity, and claims a job with `SKIP LOCKED`. One trial/proposal reserves one slot;
legacy multi-task jobs reserve two. Prefer organizations with fewer currently
reserved slots, then older jobs. This is a simple fairness heuristic, not weighted
fair queuing. No database transaction spans inference or task execution.
Within a newly submitted plan, creation timestamps tie and random UUIDs break
ties, randomizing admission order; reported repetition labels are grouping labels.

Default capacity is two, leases last 90 seconds and heartbeat every ten seconds.
An independent monotonic watchdog stops execution before its last known lease
expires even if a DB heartbeat blocks. Writes require the current unexpired claim
token. Reservations persist through lease expiry until the exact attempt's
containers are cleaned. A reaper waits an additional 60-second grace before
cleaning stale attempts. Engine failures retain reservations and stop admission;
explicit container-not-found races are harmless. Reclaims get a new token and
retain the interrupted attempt, with at most three attempts per job.

At-least-once execution means a crash can repeat provider calls; fenced database
commits do not give exactly-once billing. A generated proposal is checkpointed
before validation, and its saved version is reused across recovery. The controller
captures container exit/time evidence separately from agent-written metadata.

Experiment summaries expose per-task/repetition outcomes and paired regressions.
A score is final only if every required trial has valid benchmark results; a
missing reward or infrastructure error leaves the aggregate score null. A valid
zero reward is a completed trial. One worse candidate does not terminate the other
frozen candidates. No experiment auto-promotes a candidate; the original automatic
optimization endpoint retains its strict improvement/no-regression policy.
Held-out trial evidence is rejected by the proposal API. This guards service data
flow, not human copying of results or dataset exposure in model training.

## Capacity and deployment limits

```sh
docker compose -f compose.service.yaml exec worker python -m service.pool
docker compose -f compose.service.yaml exec worker python -m service.pool --capacity 4
docker compose -f compose.service.yaml up -d --scale worker=4
```

Choose capacity for the engine's actual RAM/CPU: each task receives one CPU and
2 GB RAM. The operator command refuses to lower capacity below current reservations.
Workers share this installation's PostgreSQL pool, artifact volume and Docker engine.
More worker processes alone do not increase sandbox concurrency beyond the pool.
The Harbor launcher serializes shared task-cache downloads and publishes each task
from a staging directory, preventing partial cache reads during concurrent startup
or worker death. Inference and task execution remain concurrent.
Legacy `service.experiments` is retained for historical reports and bypasses this
queue; do not run it concurrently with managed work. New comparisons use the API.

This implementation demonstrates horizontal worker processes on one Docker
installation. It does not provide a multi-host cluster, high availability, workload
autoscaling, an inference rate limiter or production capacity guarantees. Further
work includes object storage, sandbox placement/VM isolation, connection pooling,
provider quotas, durable audit events and artifact retention. The reproducible
failure drill and measured limits are in [OPERATIONS.md](OPERATIONS.md).
