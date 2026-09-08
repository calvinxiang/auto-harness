import json
import hashlib
import math
import os
from pathlib import Path
import signal
import re
import subprocess
import tempfile
import time

from .config import settings
from .queue import LeaseLost
from .evidence import trace_signals


def redact(value):
    text = value
    for key in ('OPENAI_API_KEY', 'E2B_API_KEY', 'BOOTSTRAP_TOKEN'):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, '[REDACTED]')
    return text


def read_text(path, limit=32000):
    try:
        with path.open('rb') as stream:
            size = path.stat().st_size
            if size <= limit:
                data = stream.read(limit)
            else:
                head = stream.read(limit // 2)
                stream.seek(-limit // 2, 2)
                data = head + b'\n... truncated ...\n' + stream.read(limit // 2)
        return redact(data.decode(errors='replace'))
    except OSError:
        return ''


def parse_results(job_dir, task_ids):
    by_task = {}
    for path in job_dir.glob('*/result.json'):
        try:
            data = json.loads(read_text(path, 500000))
            task_id = data['task_name']
            if task_id not in task_ids:
                continue
            verifier = data.get('verifier_result') or {}
            reward = (verifier.get('rewards') or {}).get('reward')
            error = data.get('exception_info') or {}
            verifier_output = read_text(path.parent / 'verifier/test-stdout.txt', 12000)
            timeout = error.get('exception_type') == 'AgentTimeoutError'
            if reward is not None:
                reward = float(reward)
                if not math.isfinite(reward) or not 0 <= reward <= 1:
                    raise ValueError('Invalid reward')
                status = 'passed' if reward >= 0.5 else 'failed'
                summary = None if status == 'passed' else 'Task verifier rejected the solution'
                if status == 'failed':
                    failures = [line.strip() for line in verifier_output.splitlines() if re.search(r'FAILED|AssertionError|Error:|E +assert', line)]
                    if failures:
                        summary = '; '.join(failures[:3])[:1000]
            elif timeout:
                reward, status, summary = 0.0, 'timeout', 'Agent exceeded its time budget'
            else:
                status, summary = 'error', 'No valid verifier result: ' + str(error.get('exception_type', 'missing_reward'))
            by_task[task_id] = {'task_id': task_id, 'status': status, 'reward': reward,
                'failure_summary': summary, 'trace': read_text(path.parent / 'agent/trace.json'),
                'trace_signals': trace_signals(read_text(path.parent / 'agent/trace.json', 2000000)),
                'agent_metadata': read_text(path.parent / 'agent/meta.json', 4000),
                'supervisor_metadata': read_text(path.parent / 'agent/execution.json', 1000),
                'verifier_output': verifier_output}
        except (ValueError, KeyError, TypeError):
            continue
    tasks = [by_task.get(t, {'task_id': t, 'status': 'error', 'reward': None,
                            'failure_summary': 'Task produced no readable result', 'trace': ''}) for t in task_ids]
    return {'dataset': 'terminal-bench@2.0', 'tasks': tasks,
            'passed': sum(t['status'] == 'passed' for t in tasks),
            'failed': sum(t['status'] in ('failed', 'timeout') for t in tasks),
            'errors': sum(t['status'] == 'error' for t in tasks),
            'score': sum(t['reward'] or 0 for t in tasks) / len(tasks)}


def docker_json(args, missing_ok=False):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        # Concurrent workers may remove a --rm container between list and inspect.
        # Only explicit absence is benign; transport/auth/daemon failures stay fatal.
        errors = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        absent = errors and all(re.match(r'(Error: No such object:|Error response from daemon: (No such container:|network .+ not found))', line) for line in errors)
        if not (missing_ok and absent):
            raise RuntimeError('Sandbox Docker engine is unavailable')
    return result.stdout


def cleanup(prefix):
    """Remove only containers whose Harbor log mount belongs to this run path."""
    ids = docker_json(['ps', '-aq']).split()
    if not ids:
        return
    containers = json.loads(docker_json(['inspect', *ids], missing_ok=True))
    projects = set()
    for container in containers:
        if any(m.get('Source', '').startswith(str(prefix).rstrip('/') + '/') for m in container.get('Mounts', [])):
            project = container['Config'].get('Labels', {}).get('com.docker.compose.project')
            if project:
                projects.add(project)
            docker_json(['rm', '-f', container['Id']], missing_ok=True)
    for project in projects:
        networks = docker_json(['network', 'ls', '-q', '--filter', f'label=com.docker.compose.project={project}']).split()
        if networks:
            docker_json(['network', 'rm', *networks], missing_ok=True)


class HarborRunner:
    def preflight(self, job, iteration, stop_event):
        """Import and exercise generated Python only in the dedicated sandbox engine."""
        if stop_event.is_set():
            raise LeaseLost('Preflight cancelled before launch')
        root = Path(settings().artifacts_dir) / 'runs' / str(job['id']) / str(job['claim_token']) / ('preflight-' + str(iteration['number']))
        root.mkdir(parents=True, exist_ok=True)
        (root / 'agent.py').write_text(iteration['agent_source'])
        fixture = Path(__file__).with_name('contract_fixture.py').read_bytes()
        (root / 'contract_fixture.py').write_bytes(fixture)
        name = 'agent-preflight-' + str(iteration['id'])
        command = ['docker', 'run', '--rm', '--name', name, '--network', 'none',
                   '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--memory', '128m', '--cpus', '1', '--pids-limit', '64', '--user', '65534:65534',
                   '--tmpfs', '/tmp:rw,nosuid,size=16m', '--mount', f'type=bind,src={root},dst=/candidate,readonly',
                   'python:3.12-slim', 'timeout', '--kill-after=2s', '15s',
                   'python', '-B', '/candidate/contract_fixture.py', '/candidate/agent.py']
        # Pulling the tiny runtime image can take longer on a first run; execution
        # itself is capped inside the container, separately from the host deadline.
        deadline = time.monotonic() + 180
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
            try:
                while process.poll() is None:
                    if stop_event.wait(0.25):
                        raise LeaseLost('Preflight interrupted')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Sandbox preflight exceeded its deadline')
                output.seek(0)
                log = redact(output.read(8000).decode(errors='replace'))
                if process.returncode == 125:
                    raise RuntimeError('Preflight Docker infrastructure failed')
                return {'status': 'passed' if process.returncode == 0 else 'failed',
                        'stage': 'sandbox_contract', 'exit_code': process.returncode, 'log': log,
                        'fixture_sha256': hashlib.sha256(fixture).hexdigest()}
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                # Named removal is scoped to this iteration, even after timeout/cancel.
                subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)

    def run(self, job, iteration, stop_event):
        if stop_event.is_set():
            raise LeaseLost('Benchmark cancelled before launch')
        cfg = settings()
        execution = job['request'].get('execution', {})
        model = execution.get('agent_model', cfg.agent_model)
        if cfg.sandbox_provider != 'docker':
            raise RuntimeError('This service currently supports Docker; E2B credential is reserved for a future backend')
        root = Path(cfg.artifacts_dir) / 'runs' / str(job['id']) / str(job['claim_token']) / str(iteration['number'])
        root.mkdir(parents=True, exist_ok=False)
        source_path = root / 'agent.py'
        source_path.write_text(iteration['agent_source'])
        cmd = ['/opt/harbor/bin/python', '-m', 'service.harbor_cli', 'run', '-d', 'terminal-bench@2.0',
               '--agent-import-path', 'service.harbor_agent:SandboxHarnessAgent',
               '--model', model, '--env', 'docker', '--jobs-dir', str(root),
               '--job-name', 'benchmark', '-n', str(min(2, len(job['request']['task_ids']))), '--max-retries', '0',
               '--override-cpus', '1', '--override-memory-mb', '2048', '--delete', '--quiet']
        for task in job['request']['task_ids']:
            cmd.extend(['--task-name', task])
        env = os.environ.copy()
        env.update(HARNESS_AGENT_SOURCE=str(source_path), PYTHONPATH='/app',
                   AGENT_MODEL=model, OPENAI_BASE_URL=cfg.openai_base_url,
                   AGENT_API=execution.get('agent_api', cfg.agent_api),
                   AGENT_REASONING_EFFORT=execution.get('agent_reasoning_effort', cfg.agent_reasoning_effort),
                   AGENT_MAX_OUTPUT_TOKENS=str(execution.get('agent_max_output_tokens', cfg.agent_max_output_tokens)),
                   AGENT_MAX_STEPS=str(execution.get('max_steps', 80)),
                   AGENT_TIMEOUT_SECONDS=str(execution.get('agent_timeout_seconds', 300)))
        deadline = time.monotonic() + cfg.benchmark_timeout_seconds
        execution_error = None
        with (root / 'harbor.log').open('w') as log:
            process = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while process.poll() is None:
                    if stop_event.wait(1):
                        raise LeaseLost('Benchmark interrupted after worker lease loss or cancellation')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Benchmark exceeded overall time budget')
            except Exception as exc:
                execution_error = exc
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                try:
                    cleanup(root)
                except Exception as exc:
                    execution_error = execution_error or exc
        results = parse_results(root / 'benchmark', job['request']['task_ids'])
        results['runner_exit_code'] = process.returncode
        results['runner_log'] = read_text(root / 'harbor.log', 12000)
        results['agent_model'] = model
        results['sandbox'] = 'docker'
        if execution_error:
            execution_error.results = results
            raise execution_error
        return results
