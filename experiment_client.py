"""Create three harness hypotheses, queue a controlled comparison, and resume by state file."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

from test_client import load_local_env


class Client:
    def __init__(self, url, token=None):
        self.url, self.token = url.rstrip('/'), token

    def request(self, method, path, body=None, headers=None):
        headers = {'Content-Type': 'application/json', **(headers or {})}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.url + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'HTTP {exc.code}: {exc.read(2000).decode(errors="replace")}') from None


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=os.environ.get('SERVICE_URL', 'http://127.0.0.1:8080'))
    parser.add_argument('--state', type=Path, default=Path('workspace/experiment-state.json'))
    parser.add_argument('--output', type=Path, default=Path('workspace/package-experiment.json'))
    parser.add_argument('--org-id', default=os.environ.get('ORG_ID'))
    parser.add_argument('--token', default=os.environ.get('API_TOKEN'))
    parser.add_argument('--task-ids', nargs='+', help='Default: full ten-task development subset')
    parser.add_argument('--heldout-task-ids', nargs='*', default=[])
    parser.add_argument('--evidence-job-ids', nargs='*', default=[])
    parser.add_argument('--review-feedback', help='Optional bounded feedback for a new proposal set')
    parser.add_argument('--repetitions', type=int, choices=[1, 2, 3], default=2)
    parser.add_argument('--model', default=os.environ.get('AGENT_MODEL', 'gpt-4.1-mini'))
    parser.add_argument('--api', choices=['chat_completions', 'responses'], default=os.environ.get('AGENT_API', 'chat_completions'))
    parser.add_argument('--reasoning-effort', default=os.environ.get('AGENT_REASONING_EFFORT', ''))
    parser.add_argument('--max-output-tokens', type=int, default=int(os.environ.get('AGENT_MAX_OUTPUT_TOKENS', '4096')))
    parser.add_argument('--prepare-only', action='store_true', help='Generate/validate variants without submitting benchmark trials')
    parser.add_argument('--timeout', type=int, default=14400)
    parser.add_argument('--poll-interval', type=float, default=5)
    args = parser.parse_args()
    client = Client(args.base_url, args.token)
    state = json.loads(args.state.read_text(encoding='utf-8')) if args.state.exists() else {'nonce': uuid4().hex}
    org_id = state.get('org_id') or args.org_id
    credentials = Path('workspace') / ('client-' + str(org_id) + '.json')
    if org_id and not client.token and credentials.exists():
        client.token = json.loads(credentials.read_text())['api_token']
    if not org_id:
        bootstrap = os.environ.get('BOOTSTRAP_TOKEN')
        if not bootstrap:
            parser.error('Set ORG_ID/API_TOKEN or BOOTSTRAP_TOKEN')
        org = client.request('POST', '/organizations', {'name': 'package-experiment-' + state['nonce'][:8],
            'owner_name': 'Experiment Client'}, {'X-Bootstrap-Token': bootstrap})
        org_id, client.token = org['id'], org['api_token']
        credentials = Path('workspace') / ('client-' + org_id + '.json')
        save(credentials, {'org_id': org_id, 'api_token': client.token})
        credentials.chmod(0o600)
    if not client.token:
        parser.error('Provide API_TOKEN for this organization')
    state['org_id'] = org_id
    state.setdefault('evidence_job_ids', args.evidence_job_ids)
    state.setdefault('review_feedback', args.review_feedback)
    state.setdefault('plan', {'name': 'package-search-' + state['nonce'][:8],
        'repetitions': args.repetitions, 'heldout_task_ids': args.heldout_task_ids,
        'profile': {'agent_model': args.model, 'agent_api': args.api,
                    'agent_reasoning_effort': args.reasoning_effort,
                    'agent_max_output_tokens': args.max_output_tokens}})
    if 'baseline_id' not in state and args.task_ids:
        state['plan']['development_task_ids'] = args.task_ids
    save(args.state, state)
    base = '/organizations/' + org_id
    if 'baseline_id' not in state:
        label = 'baseline-' + state['nonce']
        # Recover the immutable baseline if the client lost the creation response.
        existing = client.request('GET', base + '/harness-versions?limit=100')
        version = next((v for v in existing if v['label'] == label), None)
        version = version or client.request('POST', base + '/harness-versions', {'label': label})
        state['baseline_id'] = version['id']
        save(args.state, state)
    state.setdefault('proposals', {})
    for dimension in ('tools', 'context', 'skills'):
        if dimension not in state['proposals']:
            job = client.request('POST', base + '/harness-versions/' + state['baseline_id'] + '/proposals',
                {'dimension': dimension, 'evidence_job_ids': state['evidence_job_ids'], 'review_feedback': state['review_feedback']},
                {'Idempotency-Key': state['nonce'] + '-' + dimension})
            state['proposals'][dimension] = job['id']
            save(args.state, state)
    deadline = time.monotonic() + args.timeout
    last = None
    while True:
        proposals = {d: client.request('GET', base + '/jobs/' + job_id) for d, job_id in state['proposals'].items()}
        status = ', '.join(d + '=' + j['status'] for d, j in proposals.items())
        if status != last:
            print('Proposals: ' + status, file=sys.stderr)
            last = status
        if all(j['status'] in ('succeeded', 'failed', 'cancelled') for j in proposals.values()):
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Proposals continue on the server; resume using the same --state path')
        time.sleep(args.poll_interval)
    version_ids = [state['baseline_id']] + [j['output']['version_id'] for j in proposals.values()
                                            if (j.get('output') or {}).get('version_id')]
    report = {'org_id': org_id, 'proposals': proposals,
              'versions': [client.request('GET', base + '/harness-versions/' + v) for v in version_ids]}
    save(args.output, report)
    if any(j['status'] != 'succeeded' for j in proposals.values()):
        print('A proposal failed; evidence retained in ' + str(args.output), file=sys.stderr)
        return 1
    if args.prepare_only:
        print('Variants validated. Resume without --prepare-only to evaluate. Report: ' + str(args.output))
        return 0
    if 'experiment_id' not in state:
        experiment = client.request('POST', base + '/experiments', {**state['plan'], 'version_ids': version_ids},
                                    {'Idempotency-Key': state['nonce'] + '-comparison'})
        state['experiment_id'] = experiment['id']
        save(args.state, state)
    last = None
    while True:
        experiment = client.request('GET', base + '/experiments/' + state['experiment_id'])
        status = (experiment['status'], sum(t['status'] in ('succeeded', 'failed', 'cancelled') for t in experiment['trials']))
        if status != last:
            print(f'Experiment {experiment["id"]}: {status[0]}, {status[1]}/{len(experiment["trials"])} trials finished', file=sys.stderr)
            last = status
        if experiment['status'] != 'running':
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Experiment continues on the server; resume using the same --state path')
        time.sleep(args.poll_interval)
    report['experiment'] = experiment
    report['history'] = {t['job_id']: client.request('GET', base + '/jobs/' + t['job_id'] + '/iterations')
                         for t in experiment['trials']}
    save(args.output, report)
    print(json.dumps({'experiment_id': experiment['id'], 'status': experiment['status'],
                      'summary': experiment['summary'], 'full_history': str(args.output)}, indent=2))
    return 0 if experiment['status'] == 'succeeded' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
