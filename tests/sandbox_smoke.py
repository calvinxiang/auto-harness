"""Real Harbor + Docker + installed runtime; deterministic HTTP fixture, no LLM cost.

This verifies isolation and trace collection, NOT agent benchmark performance.
Run with: docker compose -f compose.service.yaml run --rm --no-deps worker python -m tests.sandbox_smoke
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import shlex
import threading
from uuid import uuid4

from service.agent_source import baseline_code, render_code, validate_code
from service.config import settings
from service.runner import HarborRunner


class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if any(m['role'] == 'tool' for m in body['messages']):
            message = {'role': 'assistant', 'content': 'Smoke test completed; no benchmark solution attempted.'}
        else:
            daemon_check = (
                'import urllib.request, urllib.error, ssl\n'
                'try:\n'
                f' urllib.request.urlopen("https://{socket.gethostbyname("sandbox-engine")}:2376/_ping", context=ssl._create_unverified_context(), timeout=5)\n'
                'except (urllib.error.URLError, ssl.SSLError, ConnectionError, TimeoutError):\n'
                ' print("daemon-access-denied")\n'
                'else:\n'
                ' raise SystemExit("Unexpected unauthenticated Docker access")\n'
            )
            command = ("test ! -S /var/run/docker.sock && test ! -f /certs/client/key.pem && "
                       "test -z \"$DATABASE_URL\" && test -z \"$E2B_API_KEY\" && test -z \"$BOOTSTRAP_TOKEN\" && "
                       "test -z \"$DOCKER_HOST\" && python3 -c " + shlex.quote(daemon_check) + " && printf 'sandbox-smoke-ok\\n'")
            assert any(t['function']['name'] == 'run_command' for t in body['tools'])
            message = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'smoke_call', 'type': 'function', 'function': {'name': 'run_command', 'arguments': json.dumps({'command': command})}}]}
        data = json.dumps({'choices': [{'message': message}], 'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    server = ThreadingHTTPServer(('0.0.0.0', 0), ModelFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((socket.gethostbyname('sandbox-engine'), 2376))
        address = probe.getsockname()[0]
    os.environ['OPENAI_BASE_URL'] = f'http://{address}:{server.server_port}/v1'
    os.environ['OPENAI_API_KEY'] = 'test-sandbox-key'
    os.environ['AGENT_MODEL'] = 'fixture'
    os.environ['AGENT_API'] = 'chat_completions'
    os.environ['AGENT_REASONING_EFFORT'] = ''
    os.environ['AGENT_MAX_OUTPUT_TOKENS'] = '4096'
    # Standalone smoke jobs have no database lease. Keep their artifacts outside
    # the production worker's stale-job sweep while the live service is running.
    os.environ['ARTIFACTS_DIR'] = '/artifacts/smoke'
    settings.cache_clear()
    # A deterministic CODE change adds a tool and dispatch branch, exercising more
    # than a replacement prompt without using paid inference or solving the task.
    code = baseline_code().replace("call['function']['name'] != 'bash'",
                                   "call['function']['name'] not in ('bash', 'run_command')")
    code += "\nTOOLS.append(json.loads(json.dumps(TOOLS[0])))\nTOOLS[-1]['function']['name'] = 'run_command'\n"
    validate_code(code)
    source, sha = render_code(code)
    job = {'id': uuid4(), 'claim_token': uuid4(), 'request': {'task_ids': ['fix-git']}}
    try:
        runner = HarborRunner()
        iteration = {'id': uuid4(), 'number': 0, 'agent_source': source}
        preflight = runner.preflight(job, iteration, threading.Event())
        assert preflight['status'] == 'passed', preflight
        results = runner.run(job, iteration, threading.Event())
        assert results['errors'] == 0, results
        trace = json.loads(results['tasks'][0]['trace'])
        tool_outputs = [m['content'] for m in trace if m['role'] == 'tool']
        assert any('sandbox-smoke-ok' in output and 'exit code: 0' in output for output in tool_outputs), tool_outputs
        assert any('daemon-access-denied' in output for output in tool_outputs), tool_outputs
        assert results['errors'] == 0, results
        assert any(c['function']['name'] == 'run_command' for m in trace for c in m.get('tool_calls', []))
        print(json.dumps({'smoke': 'passed', 'job_id': str(job['id']), 'isolation_checks': 'passed',
                          'code_change': 'new tool and dispatch', 'preflight': preflight['status'],
                          'verifier_status': results['tasks'][0]['status'], 'uses_real_llm': False}))
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
