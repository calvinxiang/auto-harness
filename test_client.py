"""Submit, poll and print complete iteration history using Python's standard library."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4


def load_local_env():
    path = Path(__file__).with_name('.env')
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=os.environ.get('SERVICE_URL', 'http://localhost:8080'))
    parser.add_argument('--org-id', default=os.environ.get('ORG_ID'))
    parser.add_argument('--token', default=os.environ.get('API_TOKEN'))
    parser.add_argument('--task-ids', nargs='+', help='Default: the full documented 10-task subset')
    parser.add_argument('--max-iterations', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=14400)
    parser.add_argument('--poll-interval', type=float, default=3)
    parser.add_argument('--job-id', help='Resume polling an existing job; requires organization and API token')
    args = parser.parse_args()

    def request(method, path, body=None, extra=None):
        headers = {'Content-Type': 'application/json', **(extra or {})}
        if args.token:
            headers['Authorization'] = 'Bearer ' + args.token
        req = urllib.request.Request(args.base_url.rstrip('/') + path,
            data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'HTTP {exc.code}: {exc.read(2000).decode(errors="replace")}') from None

    if not args.org_id or not args.token:
        bootstrap = os.environ.get('BOOTSTRAP_TOKEN')
        if not bootstrap:
            parser.error('Set ORG_ID and API_TOKEN, or BOOTSTRAP_TOKEN to create a demo organization')
        if args.job_id:
            parser.error('--job-id requires --org-id and --token (or corresponding env vars)')
        org = request('POST', '/organizations', {'name': 'test-client-' + uuid4().hex[:8], 'owner_name': 'Test Client'}, {'X-Bootstrap-Token': bootstrap})
        args.org_id, args.token = org['id'], org['api_token']
        # Store only local client provisioning credentials, never print them in summaries.
        output = Path('workspace/client-' + args.org_id + '.json')
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps({'org_id': args.org_id, 'api_token': args.token}))
        try:
            output.chmod(0o600)
        except OSError:
            pass
        print(f'Created organization {args.org_id}; credentials saved in {output}', file=sys.stderr)
    base = f'/organizations/{args.org_id}/jobs'
    if args.job_id:
        job = request('GET', base + '/' + args.job_id)
    else:
        body = {'max_iterations': args.max_iterations}
        if args.task_ids:
            body['task_ids'] = args.task_ids
        job = request('POST', base, body, {'Idempotency-Key': str(uuid4())})
    deadline = time.monotonic() + args.timeout
    last_status = None
    while True:
        status = (job['status'], job['next_iteration'])
        if status != last_status:
            print(f'Job {job["id"]}: {status[0]}, completed iterations: {status[1]}', file=sys.stderr)
            last_status = status
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f'Polling timed out; job {job["id"]} continues. Resume with --job-id, --org-id and --token.')
        time.sleep(args.poll_interval)
        job = request('GET', base + '/' + job['id'])
    history = request('GET', base + '/' + job['id'] + '/iterations')
    print(json.dumps({'job': job, 'iterations': history}, indent=2))
    return 0 if job['status'] == 'succeeded' else 1


if __name__ == '__main__':
    try:
        if '--experiment' in sys.argv:
            sys.argv.remove('--experiment')
            from experiment_client import main
        sys.exit(main())
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
