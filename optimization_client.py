"""Submit an automatic package search, resume polling, and export full history."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from experiment_client import Client, save
from test_client import load_local_env


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=os.environ.get('SERVICE_URL', 'http://127.0.0.1:8080'))
    parser.add_argument('--state', type=Path, default=Path('workspace/optimization-state.json'))
    parser.add_argument('--output', type=Path, default=Path('workspace/optimization-results.json'))
    parser.add_argument('--org-id', default=os.environ.get('ORG_ID'))
    parser.add_argument('--token', default=os.environ.get('API_TOKEN'))
    parser.add_argument('--baseline-version-id')
    parser.add_argument('--task-ids', nargs='+')
    parser.add_argument('--heldout-task-ids', nargs='*', default=[])
    parser.add_argument('--dimensions', nargs='+', choices=['tools', 'context', 'skills', 'control'], default=['tools', 'context', 'skills'])
    parser.add_argument('--max-rounds', type=int, choices=[1, 2, 3], default=2)
    parser.add_argument('--patience', type=int, choices=[1, 2, 3], default=2)
    parser.add_argument('--repetitions', type=int, choices=[1, 2], default=1)
    parser.add_argument('--timeout', type=int, default=7200)
    parser.add_argument('--poll-interval', type=float, default=3)
    parser.add_argument('--model', default=os.environ.get('AGENT_MODEL', 'gpt-4.1-mini'))
    parser.add_argument('--api', choices=['chat_completions', 'responses'], default=os.environ.get('AGENT_API', 'chat_completions'))
    parser.add_argument('--reasoning-effort', default=os.environ.get('AGENT_REASONING_EFFORT', ''))
    parser.add_argument('--max-output-tokens', type=int, default=int(os.environ.get('AGENT_MAX_OUTPUT_TOKENS', '4096')))
    args = parser.parse_args()
    state = json.loads(args.state.read_text()) if args.state.exists() else {'nonce': uuid4().hex}
    client = Client(args.base_url, args.token)
    org_id = state.get('org_id') or args.org_id
    credentials = Path('workspace') / ('client-' + str(org_id) + '.json')
    if org_id and not client.token and credentials.exists():
        client.token = json.loads(credentials.read_text())['api_token']
    if not org_id:
        bootstrap = os.environ.get('BOOTSTRAP_TOKEN')
        if not bootstrap:
            parser.error('Set ORG_ID/API_TOKEN or BOOTSTRAP_TOKEN')
        org = client.request('POST', '/organizations', {'name': 'search-' + state['nonce'][:8], 'owner_name': 'Search Client'},
                             {'X-Bootstrap-Token': bootstrap})
        org_id, client.token = org['id'], org['api_token']
        credentials = Path('workspace') / ('client-' + org_id + '.json')
        save(credentials, {'org_id': org_id, 'api_token': client.token})
        credentials.chmod(0o600)
    if not client.token:
        parser.error('Provide API_TOKEN for this organization')
    state['org_id'] = org_id
    if 'plan' not in state:
        state['plan'] = {'name': 'search-' + state['nonce'][:8], 'baseline_version_id': args.baseline_version_id,
            'heldout_task_ids': args.heldout_task_ids, 'dimensions': args.dimensions,
            'max_rounds': args.max_rounds, 'patience': args.patience, 'repetitions': args.repetitions,
            'profile': {'agent_model': args.model, 'agent_api': args.api,
                        'agent_reasoning_effort': args.reasoning_effort, 'agent_max_output_tokens': args.max_output_tokens}}
        if args.task_ids:
            state['plan']['development_task_ids'] = args.task_ids
    save(args.state, state)
    base = '/organizations/' + org_id
    if 'run_id' not in state:
        run = client.request('POST', base + '/optimization-runs', state['plan'], {'Idempotency-Key': state['nonce']})
        state['run_id'] = run['id']
        save(args.state, state)
    deadline, last = time.monotonic() + args.timeout, None
    while True:
        run = client.request('GET', base + '/optimization-runs/' + state['run_id'])
        trials = [t for exp in run['experiments'].values() for t in exp['trials']]
        status = (run['status'], run['phase'], run['round_number'], sum(t['status'] in ('succeeded', 'failed', 'cancelled') for t in trials), len(trials))
        if status != last:
            print(f'Optimization {run["id"]}: {status[0]}, {status[1]}, round {status[2]}, {status[3]}/{status[4]} trials finished', file=sys.stderr)
            last = status
        if run['status'] != 'running':
            break
        if time.monotonic() > deadline:
            save(args.output, {'run': run, 'complete': False})
            raise TimeoutError('Search continues on the server; resume with the same --state or use its cancellation endpoint')
        time.sleep(args.poll_interval)
    version_ids = {run['initial_version_id'], run['best_version_id']}
    for exp in run['experiments'].values():
        version_ids.update(exp['request']['version_ids'])
    for row in run['rounds']:
        version_ids.update(j['output']['version_id'] for j in row['proposals'] if (j.get('output') or {}).get('version_id'))
    report = {'run': run, 'complete': True,
              'versions': [client.request('GET', base + '/harness-versions/' + v) for v in sorted(version_ids)],
              'history': {t['job_id']: client.request('GET', base + '/jobs/' + t['job_id'] + '/iterations') for t in trials}}
    save(args.output, report)
    print(json.dumps({'run_id': run['id'], 'status': run['status'], 'stop_reason': run['stop_reason'],
                      'best_version_id': run['best_version_id'], 'best_development_score': run['best_score'],
                      'rounds': len(run['rounds']), 'full_history': str(args.output)}, indent=2))
    return 0 if run['status'] == 'succeeded' else 1


if __name__ == '__main__':
    sys.exit(main())
