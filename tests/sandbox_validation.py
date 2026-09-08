"""Exercise rejected Python candidates in real offline containers; no paid calls."""
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
    for number, (name, code) in enumerate(cases.items()):
        validate_code(code)
        source, _ = render_code(code)
        job = {'id': uuid4(), 'claim_token': uuid4()}
        iteration = {'id': uuid4(), 'number': number, 'agent_source': source}
        result = runner.preflight(job, iteration, Event())
        assert result['status'] == 'failed', (name, result)
        outcomes[name] = {'status': result['status'], 'exit_code': result['exit_code']}
    assert outcomes['infinite_loop']['exit_code'] in (124, 137)
    print(json.dumps({'sandbox_validation': 'passed', 'rejected_candidates': outcomes}))


if __name__ == '__main__':
    main()
