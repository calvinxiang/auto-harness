"""Operator inspection of saved request/trace evidence. Does not execute agent code."""
import argparse
import json
from pathlib import Path
from uuid import UUID

from .config import settings
from .db import connect


def inspect(experiment_id):
    with connect() as conn:
        rows = conn.execute('''SELECT t.*,v.dimension,j.claim_token,j.status FROM experiment_trials t
            JOIN jobs j ON j.id=t.job_id JOIN harness_versions v ON v.id=t.version_id
            WHERE t.experiment_id=%s ORDER BY t.split,t.repetition,t.task_id,v.dimension''', (experiment_id,)).fetchall()
    evidence = []
    for row in rows:
        if row['status'] not in ('succeeded', 'failed'):
            continue
        root = Path(settings().artifacts_dir) / 'runs' / str(row['job_id']) / str(row['claim_token'])
        paths = list(root.glob('0/benchmark/*/agent/requests.json'))
        record = {k: str(row[k]) if k in ('job_id', 'version_id') else row[k]
                  for k in ('job_id', 'version_id', 'dimension', 'task_id', 'split', 'repetition', 'status')}
        record['request_evidence_present'] = bool(paths)
        for path in paths:
            requests = json.loads(path.read_text())
            trace = json.loads(path.with_name('trace.json').read_text())
            meta = json.loads(path.with_name('meta.json').read_text())
            originals = {m['tool_call_id']: str(m['content']) for m in trace if m.get('role') == 'tool'}
            compactions = {}
            for request in requests:
                for message in request['messages']:
                    if message.get('role') == 'tool':
                        call_id = message['tool_call_id']
                        original = originals.get(call_id, '')
                        sent = str(message['content'])
                        if len(sent) < len(original):
                            compactions[call_id] = {'original_chars': len(original), 'sent_chars': len(sent)}
            structured = 0
            for value in originals.values():
                try:
                    parsed = json.loads(value)
                    structured += isinstance(parsed, dict) and all(k in parsed for k in ('ok', 'exit_code', 'timed_out'))
                except ValueError:
                    pass
            record.update(model_calls=meta.get('model_calls'), input_tokens=meta.get('input_tokens'),
                output_tokens=meta.get('output_tokens'), reasoning_tokens=meta.get('reasoning_tokens'),
                asset_events=meta.get('asset_events', []), structured_status_results=structured,
                compacted_tool_results=list(compactions.values()),
                request_message_chars=[len(json.dumps(r['messages'])) for r in requests])
        evidence.append(record)
    return {'experiment_id': str(experiment_id), 'evidence': evidence,
            'note': 'Tool compaction compares actual sent messages to separately logged tool results; skill reads establish loading, not compliance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('experiment_id', type=UUID)
    args = parser.parse_args()
    print(json.dumps(inspect(args.experiment_id), indent=2))


if __name__ == '__main__':
    main()
