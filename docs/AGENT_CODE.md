# Editable Python agents

The optimizer changes an agent module, not just a system prompt. The starting
module is `service/agent_policy.py`, adapted from the original TerminalBench bash
agent. It is packaged with `service/runtime.py` as a standalone runnable snapshot.
Only the sandbox executes the proposed module.

## Contract

Define a literal `AGENT_INSTRUCTION` string and `run_agent(api, instruction)`.
The function owns its messages, tools, helpers and control flow. It returns when
finished. The API provides:

| Method | Behavior |
|---|---|
| `api.model(messages, tools)` | Return an OpenAI-style assistant message dict using the configured model; at most 80 calls |
| `api.bash(command)` | Execute a command, returning bounded stdout/stderr, exit status and timeout information |
| `api.tool_result(call_id, content)` | Record each tool result in the independent execution trace |

Retain a bash tool with a `command` string. Additional tools can support planning,
file inspection/editing, structured command results or working memory. The agent
can change how it selects tools, compacts context, detects repetition, retries,
or verifies completion. Use `api.model` for inference, `api.bash` for shell commands,
and record every tool result. Context changes must preserve complete assistant/tool
groups. No new service or third-party dependency is necessary.

The runtime records model usage, an event trace, and actual requests after context
edits. A model-call budget, per-command timeout and outer process watchdog bound
execution. Harbor controls task setup and verification. The optimizer cannot edit
the worker, runtime, adapter, database or benchmark through its response schema.

The runtime supports Chat Completions and Responses behind the same policy API.
Responses retains the original output items, including encrypted reasoning and
assistant phases, for unmodified messages still in the policy's context. Removing
a complete group during compaction also removes its native items; editing an
assistant message reconstructs that message without replaying stale reasoning.
Transport, reasoning effort and output-token allowance are operator settings saved
in the job configuration. Truncated, refused or empty model responses raise an
error after saving their usage and completion status; partial tool calls are never
returned to the policy for execution. This transport is for synchronous function
tools; it does not add hosted tools or asynchronous tool dispatch to the policy.

## Validation and history

1. Parse the JSON proposal containing `diagnosis`, `rationale` and `agent_code`.
2. Persist the proposal, module, runnable snapshot, hash and diff before validation.
3. Parse/compile Python without executing it in the service. Check the entry point,
   prompt literal, supported imports and explicit benchmark/credential references.
4. Run an offline contract fixture in a temporary container with no network or
   credentials, a read-only filesystem, limited resources and a 15-second timeout.
5. Run the real benchmark only after validation passes, then compare with the best
   version using the same tasks, model and budgets.

Syntax or contract failures remain visible in the iteration history. They stop the
job as `failed` with `invalid_candidate`; the previous best pointer remains intact.
An interrupted benchmark retries the exact saved runnable snapshot, including its
runtime, instead of silently rebuilding it from newer local files. Historical
prompt-only records remain readable; their new code/validation fields are null.

The offline driver is `service/contract_fixture.py`. It simulates a bash command,
then text completion or the explicit `finish` tool convention. Its hash is saved
with validation results, separately from the scored agent source. A genuinely new
interaction protocol may require additional fixture support; the smoke check does
not simulate arbitrary agent behavior.

## Generalization and limits

The optimizer is instructed to fix general behavior and avoid task-specific answers,
task IDs, fixture access and verifier manipulation. The static check catches obvious
violations but is not an adversarial Python sandbox or a proof of generalization.
Python in a task container can access that container; a malicious agent could bypass
in-process conventions. The external watchdog and container resources are separate
controls. Production hostile-code hosting requires stronger VM/egress boundaries.

The API subset is a development set used for both feedback and comparison.
The operator workflow in [EXPERIMENTS.md](EXPERIMENTS.md) adds repeated trials and
a separate evaluation set while retaining frozen source versions. More tools alone
do not guarantee better results: each proposed change must be evaluated, and
rejected versions stay in history.
