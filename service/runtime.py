"""Standalone sandbox agent adapted from agent/templates/terminal_bench.py.

Preserves the original bash tool loop, baseline prompt, 80 step limit and output
truncation. Uses a stdlib OpenAI-compatible HTTP client so task images need only
Python, not Harbor or service/database dependencies. Never imported by the worker
to run an agent. Only the AGENT_INSTRUCTION literal is optimized.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

AGENT_INSTRUCTION = '__SYSTEM_PROMPT__'
MAX_STEPS = 80
MAX_OUTPUT_CHARS = 8000
TOOLS = [{'type': 'function', 'function': {
    'name': 'bash', 'description': 'Execute a bash command in the container. Returns stdout and stderr.',
    'parameters': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': 'The bash command to execute.'}}, 'required': ['command']},
}}]


def truncate(text, limit=MAX_OUTPUT_CHARS):
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f'\n... [{len(text)-limit} chars truncated] ...\n' + text[-half:]


def call_model(messages):
    data = json.dumps({'model': os.environ['AGENT_MODEL'], 'messages': messages,
                       'tools': TOOLS, 'tool_choice': 'auto', 'max_completion_tokens': 4096}).encode()
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


def main():
    def budget_exceeded(*_):
        raise TimeoutError('Agent time budget exceeded')
    signal.signal(signal.SIGTERM, budget_exceeded)
    messages = [{'role': 'system', 'content': AGENT_INSTRUCTION},
                {'role': 'user', 'content': 'Task:\n' + Path('/opt/harness/instruction.txt').read_text()}]
    trace = Path('/logs/agent/trace.json')
    meta = {'input_tokens': 0, 'output_tokens': 0, 'stop_reason': 'max_steps'}
    try:
        for step in range(MAX_STEPS):
            response = call_model(messages)
            usage = response.get('usage', {})
            meta['input_tokens'] += usage.get('prompt_tokens', 0)
            meta['output_tokens'] += usage.get('completion_tokens', 0)
            message = response['choices'][0]['message']
            calls = message.get('tool_calls') or []
            messages.append({k: message[k] for k in ('role', 'content', 'tool_calls') if k in message})
            trace.write_text(json.dumps(messages))
            if not calls:
                meta['stop_reason'] = 'agent_declared_complete'
                break
            for call in calls:
                try:
                    args = json.loads(call['function']['arguments'])
                    if call['function']['name'] != 'bash' or not isinstance(args.get('command'), str):
                        raise ValueError('Invalid bash tool call')
                    result = bash(args['command'])
                except (ValueError, TypeError) as exc:
                    result = str(exc)
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': truncate(result)})
                trace.write_text(json.dumps(messages))
    except Exception as exc:
        meta['error'] = str(exc)[:1000]
        meta['stop_reason'] = 'agent_error'
        raise
    finally:
        trace.write_text(json.dumps(messages))
        Path('/logs/agent/meta.json').write_text(json.dumps(meta))


if __name__ == '__main__':
    main()
