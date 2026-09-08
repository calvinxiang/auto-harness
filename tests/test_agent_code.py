from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import json
import pytest

from service import queue
from service.agent_source import AgentValidationError, baseline_code, validate_code
from service.api import app
from service.optimizer import propose
from service.runtime import AgentAPI, CallBudgetExceeded, check_messages
from service import runtime
from service.worker import process_job
from .test_service import PreflightFixture, result, setup_org


def test_source_validation_never_executes_module(tmp_path):
    marker = tmp_path / 'must-not-exist'
    code = baseline_code() + '\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
    assert validate_code(code)
    assert not marker.exists()


@pytest.mark.parametrize('extra', [
    '\nimport urllib.request\n',
    '\nANSWER_FOR = "fix-git"\n',
    '\nVERIFIER = "/tests/test_outputs.py"\n',
    '\nTOKEN = "OPENAI_API_KEY"\n',
    '\ndef broken(:\n',
])
def test_source_review_guards(extra):
    with pytest.raises(AgentValidationError):
        validate_code(baseline_code() + extra)


@pytest.mark.parametrize('stage', ['static', 'sandbox_contract'])
def test_invalid_candidate_preserves_code_and_best_result(stage):
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        job_id = client.post(base + '/jobs', headers=headers,
                             json={'task_ids': ['fix-git', 'regex-log']}).json()['id']
        candidate = baseline_code() + ('\ndef broken(:\n' if stage == 'static' else '\nraise RuntimeError("broken candidate")\n')
        class Runner(PreflightFixture):
            calls = 0
            def preflight(self, job, iteration, stop):
                return {'status': 'failed' if iteration['number'] else 'passed', 'stage': 'sandbox_contract'}
            def run(self, job, iteration, stop):
                self.calls += 1
                return result(job['request']['task_ids'], 1)
        runner = Runner()
        process_job(queue.claim(), runner, lambda *_: {
            'diagnosis': 'Candidate fixture', 'rationale': 'Test retention', 'agent_code': candidate})
        job = client.get(base + '/jobs/' + job_id, headers=headers).json()
        records = client.get(base + '/jobs/' + job_id + '/iterations', headers=headers).json()
        assert runner.calls == 1  # Invalid code never reaches paid benchmark execution.
        assert job['stop_reason'] == 'invalid_candidate'
        assert job['best_score'] == 0.5
        assert job['best_iteration_id'] == records[0]['id']
        assert records[1]['agent_code'] == candidate
        assert records[1]['validation']['stage'] == stage
        assert records[1]['accepted'] is False


def test_optimizer_receives_code_and_returns_tool_change(monkeypatch):
    code = baseline_code()
    candidate = code.replace("'Execute a bash command in the container. Returns stdout and stderr.'",
                             "'Execute a command; include an explanation of the intended result.'")
    def post(*_, **kwargs):
        context = json.loads(kwargs['json']['messages'][1]['content'])
        assert context['current_agent_code'] == code
        assert context['failure_evidence'][0]['failure_summary'] == 'Repeated commands'
        assert context['failure_evidence'][0]['agent_metadata'] == '{"model_calls":80,"stop_reason":"max_steps"}'
        assert context['previously_passing_tasks'] == ['fix-git']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'diagnosis': 'Repeated commands', 'rationale': 'Explain intended effect', 'agent_code': candidate})}}]})
    monkeypatch.setattr(httpx.Client, 'post', post)
    proposal = propose({'prompt': 'baseline', 'agent_code': code, 'results': {'tasks': [{
        'task_id': 'regex-log', 'status': 'failed', 'failure_summary': 'Repeated commands',
        'agent_metadata': '{"model_calls":80,"stop_reason":"max_steps"}', 'trace': 'observed trace'},
        {'task_id': 'fix-git', 'status': 'passed'}]}}, [])
    assert proposal['agent_code'] == candidate


def test_context_compaction_must_preserve_tool_groups():
    call = {'role': 'assistant', 'tool_calls': [{'id': 'one'}]}
    output = {'role': 'tool', 'tool_call_id': 'one', 'content': 'done'}
    check_messages([{'role': 'user', 'content': 'task'}, call, output])
    for messages in [[call], [output], [call, {'role': 'user', 'content': 'next'}], [call, output, output]]:
        with pytest.raises(ValueError):
            check_messages(messages)


def test_context_edits_do_not_rewrite_trace_and_call_budget_is_enforced(monkeypatch):
    api = AgentAPI()
    monkeypatch.setattr(api, 'save', lambda: None)
    monkeypatch.setattr(runtime, 'MAX_STEPS', 1)
    monkeypatch.setattr(runtime, 'call_model', lambda *_: {
        'choices': [{'message': {'role': 'assistant', 'content': 'Original response'}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 2}})
    messages = [{'role': 'system', 'content': 'Original instructions'}, {'role': 'user', 'content': 'Task'}]
    tools = [{'type': 'function', 'function': {'name': 'bash'}}]
    response = api.model(messages, tools)
    response['content'] = 'Compacted'
    messages[0]['content'] = 'Changed'
    tools[0]['function']['name'] = 'changed'
    assert api.trace[0]['content'] == 'Original instructions'
    assert api.trace[-1]['content'] == 'Original response'
    assert api.requests[0]['tools'][0]['function']['name'] == 'bash'
    assert api.meta['input_tokens'] == 10
    with pytest.raises(CallBudgetExceeded):
        api.model(messages, tools)
