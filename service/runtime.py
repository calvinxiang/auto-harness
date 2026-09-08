"""Sandbox runtime bundled with a versioned, editable Python agent policy.

The service packages POLICY_SOURCE as data; only this sandbox entry point executes
it. Model transport, call accounting and process watchdogs belong to the runtime.
"""
import json
import importlib.util
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
BUNDLE_FILES = {}
BUNDLE_ENTRYPOINT = 'agent.py'
BUNDLE_SKILLS = []
BUNDLE_ROOT = None
MAX_STEPS = int(os.environ.get('AGENT_MAX_STEPS', '80'))
MAX_OUTPUT_CHARS = 8000


def truncate(text, limit=MAX_OUTPUT_CHARS):
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f'\n... [{len(text)-limit} chars truncated] ...\n' + text[-half:]


def request_model(endpoint, payload):
    request = urllib.request.Request(os.environ['OPENAI_BASE_URL'].rstrip('/') + endpoint,
        data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})
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


def message_key(message):
    return json.dumps(message, sort_keys=True)


def responses_input(messages, saved_outputs):
    """Replay native output items, including encrypted reasoning and message phase.

    Only replay items for assistant messages still present and unmodified in the
    policy's context. Context compaction must not silently restore dropped turns.
    """
    items = []
    for message in messages:
        role = message['role']
        if role == 'tool':
            items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'],
                          'output': message['content']})
        elif role == 'assistant':
            native = saved_outputs.get(message_key(message))
            if native is not None:
                items.extend(native)
                continue
            if message.get('content'):
                items.append({'role': role, 'content': message['content']})
            for call in message.get('tool_calls') or []:
                items.append({'type': 'function_call', 'call_id': call['id'],
                              'name': call['function']['name'], 'arguments': call['function']['arguments']})
        else:
            items.append({'role': role, 'content': message['content']})
    return items


def call_model(messages, tools, saved_outputs=None):
    model = os.environ['AGENT_MODEL']
    effort = os.environ.get('AGENT_REASONING_EFFORT', '')
    budget = int(os.environ.get('AGENT_MAX_OUTPUT_TOKENS', '4096'))
    if os.environ.get('AGENT_API', 'chat_completions') == 'chat_completions':
        payload = {'model': model, 'messages': messages, 'tools': tools,
                   'tool_choice': 'auto', 'max_completion_tokens': budget}
        if effort:
            payload['reasoning_effort'] = effort
        return request_model('/chat/completions', payload)
    # Preserve the existing policy tool schema semantics: Responses otherwise
    # defaults to strict normalization, unlike Chat Completions.
    payload = {'model': model, 'input': responses_input(messages, saved_outputs or {}),
               'tools': [{'type': 'function', 'strict': False, **tool['function']} for tool in tools],
               'tool_choice': 'auto', 'max_output_tokens': budget, 'store': False,
               'include': ['reasoning.encrypted_content']}
    if effort:
        payload['reasoning'] = {'effort': effort}
    response = request_model('/responses', payload)
    output = response.get('output', [])
    content, calls = [], []
    refused = False
    for item in output:
        if item['type'] == 'function_call':
            calls.append({'id': item['call_id'], 'type': 'function', 'function': {
                'name': item['name'], 'arguments': item['arguments']}})
        elif item['type'] == 'message':
            for part in item.get('content', []):
                if part['type'] == 'output_text':
                    content.append(part['text'])
                elif part['type'] == 'refusal':
                    refused = True
    message = {'role': 'assistant', 'content': '\n'.join(content) or None}
    if calls:
        message['tool_calls'] = calls
    status = response.get('status')
    finish = ('tool_calls' if calls else 'stop') if status == 'completed' else status or 'missing_status'
    if refused:
        finish = 'content_filter'
    usage = response.get('usage') or {}
    return {'choices': [{'message': message, 'finish_reason': finish}],
            'model': response.get('model'), 'incomplete_details': response.get('incomplete_details'),
            'usage': {'prompt_tokens': usage.get('input_tokens', 0),
                      'completion_tokens': usage.get('output_tokens', 0),
                      'completion_tokens_details': usage.get('output_tokens_details') or {}},
            '_native_output': output, '_provider_request': payload}


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


class ModelResponseError(Exception):
    pass


class PackageAssets:
    def asset_event(self, action, name=None):
        if hasattr(self, 'meta'):
            events = self.meta.setdefault('asset_events', [])
            if len(events) < 200:
                events.append({'action': action, 'path': name})
                self.save()

    def skills(self):
        self.asset_event('discover_skills')
        return json.loads(json.dumps(BUNDLE_SKILLS))

    def asset_path(self, name):
        if BUNDLE_ROOT is None or name not in BUNDLE_FILES:
            raise ValueError('Unknown package asset')
        path = (BUNDLE_ROOT / name).resolve()
        if not path.is_relative_to(BUNDLE_ROOT.resolve()):
            raise ValueError('Asset escapes package root')
        self.asset_event('asset_path', name)
        return str(path)

    def read_asset(self, name):
        self.asset_event('read_asset', name)
        return Path(self.asset_path(name)).read_text()


def load_policy():
    global BUNDLE_ROOT
    if BUNDLE_FILES:
        BUNDLE_ROOT = Path(tempfile.mkdtemp(prefix='harness-package-'))
        for name, source in BUNDLE_FILES.items():
            path = BUNDLE_ROOT / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        sys.path.insert(0, str(BUNDLE_ROOT))
        spec = importlib.util.spec_from_file_location('agent_policy', BUNDLE_ROOT / BUNDLE_ENTRYPOINT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.__dict__
    module = {'__name__': 'agent_policy'}
    exec(compile(POLICY_SOURCE, '<agent_policy>', 'exec'), module)
    return module


class AgentAPI(PackageAssets):
    def __init__(self):
        self.trace = []
        self.requests = []
        self.saved_outputs = {}
        self.meta = {'input_tokens': 0, 'output_tokens': 0, 'model_calls': 0,
                     'reasoning_tokens': 0,
                     'agent_model': os.environ.get('AGENT_MODEL'),
                     'agent_api': os.environ.get('AGENT_API', 'chat_completions'),
                     'reasoning_effort': os.environ.get('AGENT_REASONING_EFFORT', ''),
                     'max_output_tokens': int(os.environ.get('AGENT_MAX_OUTPUT_TOKENS', '4096')),
                     'stop_reason': 'running'}

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
        self.meta.setdefault('request_sizes', []).append(len(json.dumps(messages)))
        if not self.trace:
            self.trace.extend(json.loads(json.dumps(messages[:2])))
        self.save()
        response = call_model(messages, tools, self.saved_outputs)
        usage = response.get('usage', {})
        self.meta['input_tokens'] += usage.get('prompt_tokens', 0)
        self.meta['output_tokens'] += usage.get('completion_tokens', 0)
        self.meta['reasoning_tokens'] += (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)
        finish = response['choices'][0].get('finish_reason', 'stop')
        self.meta['last_finish_reason'] = finish
        self.meta['resolved_model'] = response.get('model')
        self.meta['incomplete_details'] = response.get('incomplete_details')
        self.requests[-1]['response'] = {k: response.get(k) for k in ('model', 'usage', 'incomplete_details')}
        self.requests[-1]['response']['finish_reason'] = finish
        if '_provider_request' in response:
            self.requests[-1]['provider_request'] = json.loads(json.dumps(response['_provider_request']))
            self.requests[-1]['response']['output'] = json.loads(json.dumps(response['_native_output']))
        message = {k: v for k, v in response['choices'][0]['message'].items()
                   if k in ('role', 'content', 'tool_calls')}
        self.trace.append(json.loads(json.dumps(message)))
        self.save()
        # Never treat exhausted reasoning tokens or partial tool arguments as a
        # completed task. Preserve response metadata and usage before failing.
        if finish not in ('stop', 'tool_calls'):
            raise ModelResponseError('LLM response did not complete: ' + str(finish))
        if not message.get('content') and not message.get('tool_calls'):
            raise ModelResponseError('LLM returned no text or tool calls')
        if '_native_output' in response:
            self.saved_outputs[message_key(message)] = json.loads(json.dumps(response['_native_output']))
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


class FixtureAPI(PackageAssets):
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
    if '--self-test' in sys.argv:
        module = load_policy()
        fixture = FixtureAPI()
        module['run_agent'](fixture, 'Run a harmless inspection command and then finish.')
        if fixture.calls < 2 or fixture.bash_calls < 1 or not fixture.outputs:
            raise ValueError('Agent did not complete the model/tool/result contract')
        print(json.dumps({'contract': 'passed', 'model_calls': fixture.calls}))
        return
    api = AgentAPI()
    try:
        module = load_policy()
        module['run_agent'](api, Path('/opt/harness/instruction.txt').read_text())
        api.meta['stop_reason'] = 'agent_declared_complete'
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
