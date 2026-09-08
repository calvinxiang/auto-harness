"""Real Harbor cache reader race with local staged files instead of network Git."""
import json
import multiprocessing
from pathlib import Path
import tempfile
import time


def read_cache(root, staged, results, locked):
    import shutil
    from harbor.models.task.id import GitTaskId
    from harbor.tasks.client import TaskClient
    from service.harbor_cli import install_download_lock

    original_copy = shutil.copytree
    def slow_copy(source, target, **kwargs):
        target.mkdir(parents=True, exist_ok=True)
        (target / 'partial').write_text('copy in progress')
        staged.set()
        time.sleep(1)
        return original_copy(source, target, dirs_exist_ok=True)
    shutil.copytree = slow_copy
    source = Path(root) / 'source'
    source.mkdir(exist_ok=True)
    (source / 'ready').write_text('complete task')
    def stage_local_source(self, git_url, task_download_configs):
        for config in task_download_configs:
            self._copy_task_source_to_target(source, config.target_path)

    # Keep Harbor's real cache-hit selection/reader behavior; replace Git download
    # with a controlled slow copy so the race is deterministic and offline.
    TaskClient._download_tasks_from_git_url = stage_local_source
    if locked:
        install_download_lock(Path(root) / '.lock')
    path = TaskClient().download_tasks([GitTaskId(git_url='https://example.invalid/tasks',
        git_commit_id='a' * 40, path=Path('fixture-task'))], output_dir=Path(root) / 'cache')[0]
    results.put((path / 'ready').exists())


def scenario(locked, kill_writer=False):
    context = multiprocessing.get_context('spawn')
    with tempfile.TemporaryDirectory() as root:
        staged, results = context.Event(), context.Queue()
        first = context.Process(target=read_cache, args=(root, staged, results, locked))
        second = context.Process(target=read_cache, args=(root, staged, results, locked))
        first.start()
        assert staged.wait(10), 'First cache writer did not start'
        if kill_writer:
            first.terminate()
            first.join(5)
        second.start()
        first.join(15)
        second.join(15)
        assert second.exitcode == 0
        assert first.exitcode == (-15 if kill_writer else 0)
        return sorted([results.get(timeout=2) for _ in range(1 if kill_writer else 2)])


def main():
    unprotected, protected = scenario(False), scenario(True)
    assert unprotected == [False, True], unprotected
    assert protected == [True, True], protected
    recovered = scenario(True, kill_writer=True)
    assert recovered == [True], recovered
    print(json.dumps({'cache_lock': 'passed', 'without_lock_task_complete': unprotected,
                      'with_lock_task_complete': protected, 'after_writer_death_task_complete': recovered,
                      'network_calls': 0}))


if __name__ == '__main__':
    main()
