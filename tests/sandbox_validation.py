"""Exercise completion protocols and rejected code in offline containers; no paid calls."""
import json
import os
from threading import Event
from uuid import uuid4

from service.agent_source import baseline_code, render_code, validate_code
from service.config import settings
from service.runner import HarborRunner


def main():
    os.environ['ARTIFACTS_DIR'] = '/artifacts/smoke'
    settings.cache_clear()
    runner = HarborRunner()
    cases = {
        'import_failure': baseline_code() + '\nraise RuntimeError("contract rejection fixture")\n',
        'invalid_tool_history': baseline_code().replace("messages.append({'role': 'tool'", "messages.append({'role': 'user'"),
        'infinite_loop': "AGENT_INSTRUCTION = 'Contract rejection fixture'\ndef run_agent(api, instruction):\n    while True:\n        pass\n",
    }
    outcomes = {}
    completion_code = '''import json
AGENT_INSTRUCTION = 'Contract fixture.'
TOOLS = [{'function': {'name': 'bash', 'parameters': {'required': ['command']}}},
         {'function': {'name': 'finish'}}]
def run_agent(api, instruction):
    messages = [{'role': 'user', 'content': instruction}]
    while True:
        message = api.model(messages, TOOLS)
        messages.append(message)
        finished = False
        for call in message.get('tool_calls', []):
            if call['function']['name'] == 'bash':
                output = api.bash(json.loads(call['function']['arguments'])['command'])
            else:
                output, finished = 'Completed', True
            api.tool_result(call['id'], output)
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': output})
        if finished:
            return
'''
    for code in [baseline_code(), completion_code]:
        validate_code(code)
        source, _ = render_code(code)
        validation = runner.preflight({'id': uuid4(), 'claim_token': uuid4()},
            {'id': uuid4(), 'number': 0, 'agent_source': source}, Event())
        assert validation['status'] == 'passed', validation
    for number, (name, code) in enumerate(cases.items()):
        validate_code(code)
        source, _ = render_code(code)
        job = {'id': uuid4(), 'claim_token': uuid4()}
        iteration = {'id': uuid4(), 'number': number, 'agent_source': source}
        result = runner.preflight(job, iteration, Event())
        assert result['status'] == 'failed', (name, result)
        outcomes[name] = {'status': result['status'], 'exit_code': result['exit_code']}
    assert outcomes['infinite_loop']['exit_code'] in (124, 137)
    print(json.dumps({'sandbox_validation': 'passed', 'completion_protocols': ['text', 'finish_tool'],
                      'rejected_candidates': outcomes}))


if __name__ == '__main__':
    main()
