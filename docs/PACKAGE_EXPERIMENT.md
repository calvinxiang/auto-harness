# Tools, context and skills: controlled package comparison

This experiment evaluates three independently generated harness changes through
the durable experiment API. Each candidate has a frozen package, parent, manifest,
file hashes, exact runnable source and validation record. Task trials run as
separate leased jobs; the report retains every attempted trial and proposal.

**No candidate improved the pass rate.** Baseline and skills passed all nine trials
per version; tools and context each failed one development Nginx trial. All 36
trials completed on their first attempt with verifier results and zero runner
errors. The service succeeded; that status means the comparison completed, not
that every task passed.

| Version | Development passes (two repetitions) | Held-out passes | Total |
|---|---:|---:|---:|
| Baseline | 6/6 | 3/3 | 9/9 |
| Tools | 5/6 | 3/3 | 8/9 |
| Context | 5/6 | 3/3 | 8/9 |
| Skills | 6/6 | 3/3 | 9/9 |

Both regressions occurred on `nginx-request-logging` in repetition index 1.
The tools variant failed the verifier's user-agent match; the context variant
failed its status-code match. Both exited normally and passed the other seven
verifier checks. These are observed formatting failures; this small comparison
does not establish that either harness mechanism caused the regression.
Every other development trial and all twelve held-out trials passed. No version
was promoted and no failure was rerun or reassessed away.

Experiment: `b8786371-fef7-4617-9736-339126965685`, recorded 2026-09-08.
The complete structured record is [package-experiment-results.json](package-experiment-results.json).
The service and API are described in [EXPERIMENT_PLATFORM.md](EXPERIMENT_PLATFORM.md);
worker failure and capacity evidence is separate in [OPERATIONS.md](OPERATIONS.md).

To run a new comparison, configure `OPTIMIZER_MODEL=gpt-5.4` and
`OPTIMIZER_REASONING_EFFORT=medium`, recreate the services, then use:

```sh
python test_client.py --experiment --state workspace/new-comparison.json --output workspace/new-comparison-results.json --model gpt-6-astra --api responses --reasoning-effort xhigh --max-output-tokens 32768 --task-ids fix-git log-summary-date-ranges nginx-request-logging --repetitions 2 --heldout-task-ids cancel-async-tasks openssl-selfsigned-cert large-scale-text-editing
```

This generates new candidates. To evaluate the exact published versions, create
them through `POST /organizations/{org}/harness-versions` using the package
manifests in the structured report, then submit their IDs to `/experiments` with
the saved plan/profile. Use only versions sharing the same runtime hash. Exact
replay of this runtime is available at revision `4d150e8`: its `service/runtime.py`
hash was checked against all four stored versions
(`d644ad61ac9a8ba99ed2ef1b72bdd4547ad31d1ff04d6144b41763e4c5ded5bc`).
Regeneration with changed runtime defaults is a new experiment. See the API guide
for schemas.

## Frozen comparison

| Version | Change | Package SHA-256 prefix |
|---|---|---|
| Baseline | Original bash tool and agent loop, packaged without behavior changes | `9068a6bc5206` |
| Tools | Optional command purpose; structured command status, exit code, timeout and output | `82a4e4f3c5c` |
| Context | Compact long tool output sent to the model; log the full result separately | `68cec1e52ed` |
| Skills | Discover and load a separate reusable verification procedure | `97787f3a61eb` |

The tools change preserves the baseline instruction, context policy and loop.
It returns both `output` and `raw_output`, potentially increasing request size.
The context change preserves tools and stopping behavior: outputs over 6,000
characters or 120 lines are reduced to a head/tail representation. This may omit
important middle lines. The skills package declares
`skills/verification-driven-completion.md`; `agent.py` discovers and loads it
through `api.skills()` and `api.read_asset()`. This candidate uses a Markdown
procedure; the package format also supports helper modules and skill resources.

| Setting | Value |
|---|---|
| Proposal model | GPT-5.4, medium reasoning, Chat Completions |
| Trial model | GPT-6 Astra, Responses, extra-high reasoning |
| Output allowance | 32,768 tokens, including reasoning |
| Agent limits | 300 seconds, 80 model calls per task |
| Task resources | 1 CPU, 2 GB memory |
| Scheduling | Two worker processes, two globally admitted task slots |
| Development | Three tasks, two repetitions, four versions: 24 trials |
| Held-out | Three tasks, one repetition, four versions: 12 trials |
| Promotion | None; comparison and selection are separate |

Development tasks were `fix-git` (repository repair), `log-summary-date-ranges`
(structured data extraction), and `nginx-request-logging` (service configuration
and exact output requirements). This small diagnostic subset spans different
workflows and keeps a repeated four-way comparison practical. The service's
full ten-task development subset remains available and was evaluated in the
[earlier baseline](FLAGSHIP_EXPERIMENT.md). These three tasks alone cannot
estimate performance on that full subset.

Held-out tasks were `cancel-async-tasks`, `openssl-selfsigned-cert`, and
`large-scale-text-editing`. They ran only after all development jobs terminated.
These are the existing project split and appeared in an earlier experiment;
they are **not newly unseen tasks**. The current optimizer saw only the earlier
ten-task development job `8ef90c0f-134d-4009-918c-0dcc64a92418`; no held-out
outcomes were supplied as proposal evidence or used to revise these candidates.

All candidate versions were frozen before scored execution. Same-transaction
creation timestamps tie, so random trial UUIDs determine admission order within
each split. Repetition labels identify matched trials; they do not imply a fixed
sequential run order. The same model/resource profile applies to every version.
Model names are aliases rather than dated snapshots; resolved model identifiers
and pinned task source revisions/checksums are retained with the artifact evidence.

## Proposal review

Six generation attempts occurred across two review rounds. The first tools and
skills versions passed the offline execution contract but were reviewed out
before scoring: they contained fixture-specific behavior and unrelated changes.
The first context proposal failed response validation; its usage and error were
saved, but the original generator discarded the raw malformed response, so its
exact invalid field cannot be independently checked from this record.

The generator now forbids fixture special cases, requests focused edits, accepts
review feedback, and retains bounded raw malformed output. A second tools,
context and skills proposal each passed static and offline validation and manual
review before this comparison. The structured report includes the initial
rejected versions and all six proposal outcomes. Six is the **observed count**,
not a claim that a six-generation cap was preregistered. No scored failure caused
a candidate rewrite or a replacement trial.

## Runtime and mechanism evidence

The experiment took **30m11s**, from 23:16:01 to 23:46:12 UTC, including admission,
preflight, task setup, evaluation and aggregation, excluding proposal generation.
The two slots were shared across versions; per-version trial times overlap.

| Version (nine trials each) | Model calls | Input tokens | Output tokens | Mean trial time |
|---|---:|---:|---:|---:|
| Baseline | 47 | 160,908 | 26,624 | 99.8s |
| Tools | 48 | 262,427 | 26,590 | 88.5s |
| Context | 51 | 148,351 | 27,411 | 91.3s |
| Skills | 54 | 235,535 | 32,358 | 102.1s |

These are reported agent usage totals, not billed cost. Output includes reasoning
tokens; input includes conversation history replay. Model-generated commands,
number of steps, task setup and service latency vary. Timing differences alone
do not establish a causal speedup from the harness change.

The artifact inspector compared actual model requests with separately logged tool
results. It found **39 structured status results** in the tools trials and **eight
compacted outputs** in the context trials. One output shrank from 6,062 to 1,936
characters; the other compactions and request sizes are in the structured report.
All nine skills trials recorded discovery and loading of the declared Markdown
asset. Baseline traces recorded none of these mechanisms.

The context variant used fewer aggregate input tokens than baseline in this run,
but lost one pass. The tools variant used more input tokens; its duplicated
`output`/`raw_output` content is a plausible contributor, not an isolated causal
measurement. The skills variant tied the baseline while using more calls and
tokens. The perfect baseline on this small subset creates a ceiling: this run
can reveal regressions and overhead, but cannot demonstrate a higher pass rate.

All 36 recorded task sources point to TerminalBench revision
`69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`. The exact source URL, task checksum,
resolved model (`gpt-6-astra`), package/source hashes and per-attempt supervisor
metadata are retained. Every supervisor recorded exit code 0 without a watchdog
timeout. There were no remaining task reservations after completion.

## Interpretation limits

Loading a skill demonstrates that its instructions reached the model; it does
not prove the model followed them. Shorter recorded tool messages demonstrate
compaction, not better reasoning. Structured status fields demonstrate a changed
tool interface, not more reliable verification. Outcome differences are noisy
with two development repetitions and one held-out repetition. A broader repeated
evaluation would be needed to attribute gains or regressions to a mechanism.

The experiment demonstrates editable tools, context and skill packages under one
durable, bounded execution system. It does not establish production throughput,
multi-host scalability, or an automatic multi-round package search/promotion
algorithm. The original `/jobs` optimization loop still provides iterative
single-module proposals with strict improvement/no-regression acceptance.
