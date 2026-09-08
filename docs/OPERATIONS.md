# Worker recovery and capacity evidence

Run `tests.operational_proof` in a worker container with a **fresh separate test
database**. It refuses production database names and refuses to clear existing
records. Use a new database name when repeating it.

```sh
docker compose -f compose.service.yaml exec db createdb -U harness harness_ops_test
docker compose -f compose.service.yaml run --rm --no-deps -e DATABASE_URL=postgresql://harness:local-development-only@db:5432/harness_ops_test worker python -m tests.operational_proof
```

The successful local run used `harness_ops2_test`; the first run exposed a Docker
list/inspect race when another worker removed a container. Cleanup now tolerates
explicit absence while failing closed for real engine errors. Both runs' database
records were retained separately. The failed run cleaned up its scoped containers.

The drill invokes real API routes through FastAPI's test client, PostgreSQL queue,
four separate worker processes, the production worker lifecycle, actual offline
package preflight and real Docker task containers. The benchmark payload is a
four-second sleep with a fixture reward, so these measurements are infrastructure
observations, **not benchmark throughput or a production load test**. There are no
paid model calls and no fixture mode in the production API.

It submits three experiments across two organizations, including duplicate
submissions; kills a worker process group during a task; replaces the worker;
cancels an active experiment; and kills another task container. Killing the group
models the deployed worker container's death while leaving its remote task
container alive. The proof checks that the reservation survives, the stale write
is rejected, the same source resumes under a new attempt, previously completed
history stays unchanged, and all containers/reservations are eventually removed.

Results on Docker Desktop with 24 CPUs and 16,317,448,192 bytes memory exposed to
the sandbox engine, one local run on 2026-09-08:

| Measurement | Observed |
|---|---:|
| Worker processes / configured task slots | 4 / 2 |
| Peak observed reserved slots / running containers | 2 / 2 |
| Lease / cleanup grace used by drill | 6s / 2s |
| Wall-clock duration | 33.61s |
| Successful trials | 9 |
| Successful fixture trials per minute | 16.07 |
| Mean successful-trial queue wait | 14.68s |
| Mean successful-trial creation-to-completion latency | 21.06s |
| Recovered attempt history | interrupted, completed |
| Final recovery / cancellation / interrupted-sandbox experiment states | succeeded / cancelled / failed |
| Remaining task containers / reservations | 0 / 0 |

Concurrency is sampled every 250ms plus Docker/DB call time. Atomic pool admission
also has independent concurrent-claim integration coverage. The drill uses shorter
leases for speed; deployment defaults are 90s lease, 10s heartbeat and 60s cleanup
grace. Completed task rewards in this drill are synthetic. Timing includes
preflight, queueing, fault injection and recovery on this machine.
Structured results: [operational-results.json](operational-results.json).
