"""Print public task metadata only, for subset selection (never solutions/tests)."""
from pathlib import Path
import json
import tomllib
from service.tasks import TASKS

if __name__ == '__main__':
    found = set()
    for path in sorted(Path('/artifacts/dataset').rglob('task.toml')):
        if path.parent.name not in TASKS:
            continue
        data = tomllib.loads(path.read_text())
        found.add(path.parent.name)
        metadata = data.get('metadata', {})
        print(json.dumps({'id': path.parent.name, 'difficulty': metadata.get('difficulty'),
            'category': metadata.get('category'), 'expert_minutes': metadata.get('expert_time_estimate_min'),
            'agent_timeout': data.get('agent', {}).get('timeout_sec')}))
    assert found == TASKS.keys(), f'Missing selected tasks: {TASKS.keys() - found}'
