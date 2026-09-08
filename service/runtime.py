"""Sandbox runtime bundled with a versioned, editable Python agent policy.

The service packages POLICY_SOURCE as data; only this sandbox entry point executes
it. Model transport, call accounting and process watchdogs belong to the runtime.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

POLICY_SOURCE = '__AGENT_CODE__'
MAX_STEPS = 80
MAX_OUTPUT_CHARS = 8000


def truncate(text, limit=MAX_OUTPUT_CHARS):
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f'\n... [{len(text)-limit} chars truncated] ...\n' + text[-half:]


def call_model(messages, tools):
    data = json.dumps({'model': os.environ['AGENT_MODEL'], 'messages': messages,
                       'tools': tools, 'tool_choice': 'auto', 'max_completion_tokens': 4096}).encode()
    request = urllib.request.Request(os.environ['OPENAI_BASE_URL'].rstrip('/') + '/chat/completions',
        data=data, headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read(2_000_000))
        except urllib.error.HTTPError as exc:
            if attempt == 2 or exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f'LLM HTTP {exc.code}') from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('LLM connection failed') from None
        time.sleep(2 ** attempt)


def bash(command):
    # Spool command output to disk and retain its head/tail without exhausting RAM.
    def limits():
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(['bash', '-lc', command], stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=True, preexec_fn=limits)
        timed_out = False
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        finally:
            # The overall agent watchdog can interrupt us while a bash child runs.
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        length = output.tell()
        output.seek(0)
        head = output.read(MAX_OUTPUT_CHARS // 2)
        if length > MAX_OUTPUT_CHARS:
            output.seek(-MAX_OUTPUT_CHARS // 2, 2)
            raw = head + b'\n... output truncated ...\n' + output.read()
        else:
            raw = head + output.read()
    return raw.decode(errors='replace') + f'\n[exit code: {proc.returncode}; timed_out: {timed_out}]'


class CallBudgetExceeded(Exception):
    pass


class AgentAPI:
    def __init__(self):
        self.trace = []
        self.requests = []
        self.meta = {'input_tokens': 0, 'output_tokens': 0, 'model_calls': 0,
                     'stop_reason': 'agent_declared_complete'}

    def save(self):
        Path('/logs/agent/trace.json').write_text(json.dumps(self.trace))
        Path('/logs/agent/requests.json').write_text(json.dumps(self.requests))
        Path('/logs/agent/meta.json').write_text(json.dumps(self.meta))

    def model(self, messages, tools):
        check_messages(messages)
        if self.meta['model_calls'] >= MAX_STEPS:
            raise CallBudgetExceeded()
        self.meta['model_calls'] += 1
        # Preserve the actual request after context edits, plus an independent event
        # trace. The optimizer cannot accidentally erase history by compacting context.
        self.requests.append({'call': self.meta['model_calls'],
                              'messages': json.loads(json.dumps(messages)),
                              'tools': json.loads(json.dumps(tools))})
        if not self.trace:
            self.trace.extend(json.loads(json.dumps(messages[:2])))
        self.save()
        response = call_model(messages, tools)
        usage = response.get('usage', {})
        self.meta['input_tokens'] += usage.get('prompt_tokens', 0)
        self.meta['output_tokens'] += usage.get('completion_tokens', 0)
        message = {k: v for k, v in response['choices'][0]['message'].items()
                   if k in ('role', 'content', 'tool_calls')}
        self.trace.append(json.loads(json.dumps(message)))
        self.save()
        return message

    def bash(self, command):
        if not isinstance(command, str):
            raise ValueError('Command must be a string')
        return bash(command)

    def tool_result(self, call_id, content):
        self.trace.append({'role': 'tool', 'tool_call_id': call_id, 'content': str(content)})
        self.save()


def check_messages(messages):
    """Check tool-call pairing, including after proposed context compaction."""
    if not isinstance(messages, list) or not messages:
        raise ValueError('Messages must be a nonempty list')
    pending = set()
    for message in messages:
        role = message['role']
        if role == 'tool':
            call_id = message['tool_call_id']
            if call_id not in pending:
                raise ValueError('Orphaned tool result')
            pending.remove(call_id)
        else:
            if pending:
                raise ValueError('Missing tool result')
            if role == 'assistant':
                calls = message.get('tool_calls') or []
                pending = {c['id'] for c in calls}
                if len(pending) != len(calls):
                    raise ValueError('Duplicate tool call ID')
    if pending:
        raise ValueError('Missing tool result')


class FixtureAPI:
    """Offline contract check. No credentials, model request or command execution."""
    def __init__(self):
        self.calls = 0
        self.bash_calls = 0
        self.outputs = []

    def model(self, messages, tools):
        check_messages(messages)
        self.calls += 1
        if self.calls > 8:
            raise ValueError('Agent did not terminate after completion responses')
        bash_tool = next(t['function'] for t in tools if t['function']['name'] == 'bash')
        if self.calls > 1:
            return {'role': 'assistant', 'content': 'The requested fixture command succeeded.'}
        args = {'command': 'printf contract-check'}
        for name in bash_tool['parameters'].get('required', []):
            if name not in args:
                args[name] = 'Inspecting the fixture environment.'
        return {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'contract-call', 'type': 'function', 'function': {
                'name': 'bash', 'arguments': json.dumps(args)}}]}

    def bash(self, command):
        if not isinstance(command, str):
            raise ValueError('Command must be a string')
        self.bash_calls += 1
        return 'contract-check\n[exit code: 0; timed_out: False]'

    def tool_result(self, call_id, content):
        self.outputs.append((call_id, content))


def main():
    def budget_exceeded(*_):
        raise TimeoutError('Agent time budget exceeded')
    signal.signal(signal.SIGTERM, budget_exceeded)
    module = {'__name__': 'agent_policy'}
    if '--self-test' in sys.argv:
        exec(compile(POLICY_SOURCE, '<agent_policy>', 'exec'), module)
        fixture = FixtureAPI()
        module['run_agent'](fixture, 'Run a harmless inspection command and then finish.')
        if fixture.calls < 2 or fixture.bash_calls < 1 or not fixture.outputs:
            raise ValueError('Agent did not complete the model/tool/result contract')
        print(json.dumps({'contract': 'passed', 'model_calls': fixture.calls}))
        return
    api = AgentAPI()
    try:
        exec(compile(POLICY_SOURCE, '<agent_policy>', 'exec'), module)
        module['run_agent'](api, Path('/opt/harness/instruction.txt').read_text())
    except CallBudgetExceeded:
        api.meta['stop_reason'] = 'max_steps'
    except Exception as exc:
        api.meta['error'] = type(exc).__name__
        api.meta['stop_reason'] = 'agent_error'
        raise
    finally:
        api.save()


if __name__ == '__main__':
    main()
