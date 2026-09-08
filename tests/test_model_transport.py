import json

import pytest

from service import runtime
from service.config import execution_config, settings
from service.experiments import check_comparison_config


TOOLS = [{'type': 'function', 'function': {'name': 'bash', 'parameters': {
    'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']}}}]


def test_responses_preserves_reasoning_phase_tool_groups_and_context_edits(monkeypatch):
    monkeypatch.setenv('AGENT_API', 'responses')
    monkeypatch.setenv('AGENT_MODEL', 'fixture-reasoner')
    monkeypatch.setenv('AGENT_REASONING_EFFORT', 'xhigh')
    monkeypatch.setenv('AGENT_MAX_OUTPUT_TOKENS', '32768')
    native = [
        {'type': 'reasoning', 'id': 'r1', 'summary': [], 'encrypted_content': 'opaque-fixture'},
        {'type': 'message', 'id': 'm1', 'role': 'assistant', 'phase': 'commentary',
         'content': [{'type': 'output_text', 'text': 'Inspecting.', 'annotations': []}]},
        {'type': 'function_call', 'id': 'f1', 'call_id': 'call-1', 'name': 'bash', 'arguments': '{"command":"pwd"}'},
        {'type': 'function_call', 'id': 'f2', 'call_id': 'call-2', 'name': 'bash', 'arguments': '{"command":"ls"}'},
    ]
    payloads = []

    def request(endpoint, payload):
        assert endpoint == '/responses'
        assert payload['reasoning'] == {'effort': 'xhigh'}
        assert payload['max_output_tokens'] == 32768
        assert payload['tools'][0]['strict'] is False
        assert payload['store'] is False
        payloads.append(json.loads(json.dumps(payload)))
        return {'status': 'completed', 'model': 'fixture-snapshot', 'output': native,
                'usage': {'input_tokens': 10, 'output_tokens': 8,
                          'output_tokens_details': {'reasoning_tokens': 5}}}

    monkeypatch.setattr(runtime, 'request_model', request)
    api = runtime.AgentAPI()
    monkeypatch.setattr(api, 'save', lambda: None)
    messages = [{'role': 'user', 'content': 'Inspect the directory.'}]
    tools = json.loads(json.dumps(TOOLS))
    answer = api.model(messages, tools)
    assert answer['tool_calls'][0]['id'] == 'call-1'
    messages.extend([answer, {'role': 'tool', 'tool_call_id': 'call-1', 'content': '/app'},
                     {'role': 'tool', 'tool_call_id': 'call-2', 'content': 'main.py'}])
    api.model(messages, TOOLS)
    assert payloads[1]['input'][1:5] == native
    assert payloads[1]['input'][5:] == [
        {'type': 'function_call_output', 'call_id': 'call-1', 'output': '/app'},
        {'type': 'function_call_output', 'call_id': 'call-2', 'output': 'main.py'}]
    api.model([{'role': 'user', 'content': 'Compacted summary; begin again.'}], TOOLS)
    assert len(payloads[2]['input']) == 1  # Deleted context is not restored by transport.
    assert api.meta['resolved_model'] == 'fixture-snapshot'
    assert api.meta['reasoning_tokens'] == 15
    assert api.meta['output_tokens'] == 24
    assert api.requests[1]['provider_request'] == payloads[1]
    tools[0]['function']['parameters']['properties']['command']['type'] = 'number'
    assert api.requests[0]['provider_request']['tools'][0]['parameters']['properties']['command']['type'] == 'string'
    answer['content'] = 'Edited summary'
    edited = runtime.responses_input([answer], api.saved_outputs)
    assert not any(i.get('type') == 'reasoning' for i in edited)


@pytest.mark.parametrize('api_kind', ['responses', 'chat_completions'])
def test_incomplete_model_output_is_not_executed_or_reported_as_completion(monkeypatch, api_kind):
    monkeypatch.setenv('AGENT_API', api_kind)
    monkeypatch.setenv('AGENT_MODEL', 'fixture')
    partial = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call',
        'type': 'function', 'function': {'name': 'bash', 'arguments': '{"command":'}}]}
    response = {'choices': [{'message': partial, 'finish_reason': 'length'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 25}}
    if api_kind == 'responses':
        response = {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
                    'output': [{'type': 'function_call', 'call_id': 'call', 'name': 'bash', 'arguments': '{"command":'}],
                    'usage': {'input_tokens': 10, 'output_tokens': 25}}
    monkeypatch.setattr(runtime, 'request_model', lambda *_: response)
    api = runtime.AgentAPI()
    monkeypatch.setattr(api, 'save', lambda: None)
    with pytest.raises(runtime.ModelResponseError):
        api.model([{'role': 'user', 'content': 'Task'}], TOOLS)
    assert api.meta['output_tokens'] == 25
    assert api.requests[-1]['response']['finish_reason'] in ('incomplete', 'length')
    assert not api.saved_outputs


def test_chat_completion_budget_and_reasoning_are_explicit(monkeypatch):
    monkeypatch.setenv('AGENT_API', 'chat_completions')
    monkeypatch.setenv('AGENT_MODEL', 'fixture')
    monkeypatch.setenv('AGENT_REASONING_EFFORT', 'high')
    monkeypatch.setenv('AGENT_MAX_OUTPUT_TOKENS', '25000')

    def request(endpoint, payload):
        assert endpoint == '/chat/completions'
        assert payload['max_completion_tokens'] == 25000
        assert payload['reasoning_effort'] == 'high'
        assert payload['tools'] == TOOLS
        return {'choices': [{'message': {'role': 'assistant', 'content': None}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr(runtime, 'request_model', request)
    api = runtime.AgentAPI()
    monkeypatch.setattr(api, 'save', lambda: None)
    with pytest.raises(runtime.ModelResponseError, match='no text or tool calls'):
        api.model([{'role': 'user', 'content': 'Task'}], TOOLS)


def test_evaluation_rejects_changed_transport_or_reasoning_settings(monkeypatch):
    monkeypatch.setenv('AGENT_API', 'responses')
    monkeypatch.setenv('AGENT_REASONING_EFFORT', 'xhigh')
    monkeypatch.setenv('AGENT_MAX_OUTPUT_TOKENS', '32768')
    settings.cache_clear()
    original = execution_config()
    for key, other in [('agent_api', 'chat_completions'), ('agent_reasoning_effort', 'low'),
                       ('agent_max_output_tokens', 4096)]:
        with pytest.raises(ValueError):
            check_comparison_config(original, {**original, key: other})
