# Worker recovery and capacity evidence

Run `tests.operational_proof` in a worker container with a **fresh separate test
database**. It refuses production database names and refuses to clear existing
records. Use a new database name when repeating it.

```sh
docker compose -f compose.service.yaml exec db createdb -U harness harness_ops_test
docker compose -f compose.service.yaml run --rm --no-deps -e DATABASE_URL=postgresql://harness:local-development-only@db:5432/harness_ops_test worker python -m tests.operational_proof
```

The final successful local run used `harness_ops3_test`, after an earlier successful
run in `harness_ops2_test`; the first run exposed a Docker
list/inspect race when another worker removed a container. Cleanup now tolerates
explicit absence while failing closed for real engine errors. All three runs' database
records were retained separately. The failed run cleaned up its scoped containers.
The final repeat used periodic parent reconciliation. A separate PostgreSQL
regression test exercises the crash window where the last child committed before
its parent status was aggregated.

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
the sandbox engine, final local run on 2026-09-08:

| Measurement | Observed |
|---|---:|
| Worker processes / configured task slots | 4 / 2 |
| Peak observed reserved slots / running containers | 2 / 2 |
| Lease / cleanup grace used by drill | 6s / 2s |
| Wall-clock duration | 30.95s |
| Successful trials | 9 |
| Successful fixture trials per minute | 17.45 |
| Mean successful-trial queue wait | 13.25s |
| Mean successful-trial creation-to-completion latency | 19.27s |
| Recovered attempt history | interrupted, completed |
| Final recovery / cancellation / interrupted-sandbox experiment states | succeeded / cancelled / failed |
| Remaining task containers / reservations | 0 / 0 |

Concurrency is sampled every 250ms plus Docker/DB call time. Atomic pool admission
also has independent concurrent-claim integration coverage. The drill uses shorter
leases for speed; deployment defaults are 90s lease, 10s heartbeat and 60s cleanup
grace. Completed task rewards in this drill are synthetic. Timing includes
preflight, queueing, fault injection and recovery on this machine.
Structured results: [operational-results.json](operational-results.json).
The earlier successful drill took 33.61s; its timing summary is preserved in the
same file. These two runs are correctness drills, not a capacity distribution.

## Concurrent cache publication

The pinned Harbor 0.1.45 task client considers a nonempty directory a cache hit,
but copies files into that directory without a cross-process lock. A second worker
can therefore observe partial task contents during a fresh download. The service
launcher wraps cache access with `flock`, stages complete task copies beside their
target and renames them into place. A killed writer releases its lock automatically;
its unpublished staging directory is removed on the next copy attempt. This does
not serialize inference or task execution. The supported benchmark uses pinned
Git task revisions; direct external Harbor invocations do not share this wrapper.

```sh
docker compose -f compose.service.yaml run --rm --no-deps worker /opt/harbor/bin/python -m tests.cache_lock_smoke
```

The test uses Harbor's actual cache-hit logic with a slow local copy instead of
network Git. Without protection, one reader returned incomplete data. With the
lock and atomic publication, both readers returned complete data; after killing
the first writer during staging, the second reader also obtained complete data.
The negative control and both protected cases passed, with zero network calls.
