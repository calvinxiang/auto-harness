import hashlib
import json
from threading import Event

import httpx
import pytest

from service.agent_source import baseline_code
from service.config import execution_config, settings
from service.evidence import trace_signals
from service.experiments import assess_agent_failure, check_comparison_config, evaluate, evaluation_plan, proposal_feedback
from service.optimizer import propose, OptimizationError
from .test_service import result


def test_trace_signals_count_observations_without_diagnosing_failure():
    call = {'role': 'assistant', 'tool_calls': [{'function': {'name': 'bash', 'arguments': '{"command":"git status"}'}}]}
    signals = trace_signals(json.dumps([call, call, {'role': 'assistant', 'content': 'Done'}]))
    assert signals['complete_trace']
    assert signals['repeated_bash_calls'] == 1
    assert signals['empty_no_tool_responses'] == 0
    assert signals['most_repeated_commands'] == [{'command': 'git status', 'count': 2}]
    assert 'failure' not in signals
    assert trace_signals('[... truncated ...]')['complete_trace'] is False


def test_holdout_disjointness_and_alternating_order():
    plan = evaluation_plan(['development'], ['heldout'], 2)
    assert [p['variant'] for p in plan] == ['baseline', 'candidate', 'candidate', 'baseline', 'baseline', 'candidate']
    assert [p['split'] for p in plan] == ['development'] * 4 + ['heldout'] * 2
    with pytest.raises(ValueError):
        evaluation_plan(['same'], ['same'], 2)


def test_comparison_fixes_agent_settings_but_allows_different_optimizer():
    original = execution_config()
    check_comparison_config(original, {**original, 'optimizer_model': 'different'})
    with pytest.raises(ValueError):
        check_comparison_config(original, {**original, 'agent_model': 'different'})


def test_heldout_results_cannot_be_recycled_into_proposal_feedback():
    previous = {'proposal': {'agent_code': 'fixture'}, 'review': {'reason': 'Valid check rejected'},
                'runs': [{'split': 'development', 'results': 'private raw results'}]}
    feedback = proposal_feedback(previous, 'fixture')
    assert 'runs' not in feedback
    previous['runs'].append({'split': 'heldout', 'results': 'held-out evidence'})
    with pytest.raises(ValueError):
        proposal_feedback(previous, 'fixture')


def test_evaluation_checkpoints_resume_without_rerunning_completed_trials(tmp_path):
    report = {'execution': execution_config(), 'development_task_ids': ['fix-git'],
              'baseline_agent_source': 'baseline', 'candidate_agent_source': 'candidate',
              'baseline_source_sha256': hashlib.sha256(b'baseline').hexdigest(),
              'candidate_source_sha256': hashlib.sha256(b'candidate').hexdigest()}
    class Runner:
        calls = 0
        def preflight(self, *_):
            return {'status': 'passed'}
        def run(self, job, iteration, stop):
            self.calls += 1
            return result(job['request']['task_ids'], 1)
    runner = Runner()
    path = tmp_path / 'report.json'
    evaluate(report, path, 2, 'Evidence review fixture', runner, Event())
    assert runner.calls == 6
    saved = json.loads(path.read_text())
    saved['runs'][1]['status'] = 'assessed'
    evaluate(saved, path, 2, 'Resume after interruption', runner, Event())
    assert runner.calls == 6
    assert saved['review']['reason'] == 'Evidence review fixture'
    with pytest.raises(ValueError):
        evaluate(saved, path, 1, 'Changed plan', runner, Event())


def test_agent_damage_assessment_retains_raw_error_and_never_awards_a_pass():
    report = {'status': 'evaluating', 'runs': [{'run_id': 'fixture', 'status': 'failed', 'results': {
        'tasks': [{'task_id': 'task', 'status': 'error', 'reward': None, 'trace': 'Evidence retained'}],
        'passed': 0, 'failed': 0, 'errors': 1, 'score': 0.0}}]}
    with pytest.raises(ValueError):
        assess_agent_failure(report, 'fixture', 'task', 'Cannot assess while still running')
    report['status'] = 'failed'
    assess_agent_failure(report, 'fixture', 'task', 'Recorded command destroyed the task filesystem')
    run = report['runs'][0]
    assert run['status'] == 'assessed'
    assert run['original_results']['tasks'][0]['status'] == 'error'
    assert run['original_results']['tasks'][0]['reward'] is None
    assert run['results']['score'] == 0 and run['results']['passed'] == 0
    assert run['results']['failed'] == 1 and run['results']['errors'] == 0
    assert run['results']['tasks'][0]['trace'] == 'Evidence retained'
    with pytest.raises(ValueError):
        assess_agent_failure(report, 'fixture', 'task', 'Cannot reassess a resolved error')


def test_optimizer_reasoning_and_trace_signals_are_explicit(monkeypatch):
    monkeypatch.setenv('OPTIMIZER_MODEL', 'gpt-5.4-2026-03-05')
    monkeypatch.setenv('OPTIMIZER_REASONING_EFFORT', 'medium')
    settings.cache_clear()
    code = baseline_code()
    def post(*_, **kwargs):
        payload = kwargs['json']
        assert payload['reasoning_effort'] == 'medium'
        context = json.loads(payload['messages'][1]['content'])
        assert context['failure_evidence'][0]['trace_signals']['empty_no_tool_responses'] == 0
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'diagnosis': 'Evidence', 'rationale': 'Fixture', 'agent_code': code + '\n# fixture'})}}]})
    monkeypatch.setattr(httpx.Client, 'post', post)
    proposal = propose({'agent_code': code, 'results': {'tasks': [{
        'task_id': 'fix-git', 'status': 'failed', 'failure_summary': 'Fixture',
        'trace_signals': {'complete_trace': True, 'empty_no_tool_responses': 0}}]}}, [])
    assert proposal['reasoning_effort'] == 'medium'


def test_truncated_optimizer_output_preserves_usage_without_raw_body(monkeypatch):
    def post(*_, **kwargs):
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length',
            'message': {'content': 'incomplete potentially private output'}}], 'usage': {'total_tokens': 12000}})
    monkeypatch.setattr(httpx.Client, 'post', post)
    with pytest.raises(OptimizationError) as caught:
        propose({'agent_code': baseline_code(), 'results': {'tasks': []}}, [])
    assert caught.value.details['finish_reason'] == 'length'
    assert caught.value.details['usage']['total_tokens'] == 12000
    assert 'private' not in str(caught.value.details)
