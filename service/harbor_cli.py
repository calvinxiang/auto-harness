"""Launch pinned Harbor with process-safe publication of its shared task cache."""
import fcntl
from functools import wraps
import os
from pathlib import Path
import shutil
import tempfile


def install_download_lock(lock_path=None):
    from harbor.constants import CACHE_DIR
    from harbor.tasks.client import TaskClient
    original = TaskClient.download_tasks
    if getattr(original, '_harness_serialized', False):
        return
    lock_path = lock_path or CACHE_DIR / '.task-download.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    @wraps(original)
    def download(self, *args, **kwargs):
        # Harbor 0.1.45 checks directory existence before a non-atomic copytree.
        # Readers must share the lock with writers, including for warm cache hits.
        # Process death closes the descriptor and releases flock automatically.
        with lock_path.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            return original(self, *args, **kwargs)

    download._harness_serialized = True
    TaskClient.download_tasks = download

    def publish(self, source_path, target_path):
        # A killed writer leaves an unpublished staging directory, never a
        # nonempty partial target that Harbor could mistake for a cache hit.
        target_path.parent.mkdir(parents=True, exist_ok=True)
        for stale in target_path.parent.glob('.harness-stage-*'):
            shutil.rmtree(stale)
        staging = Path(tempfile.mkdtemp(prefix='.harness-stage-', dir=target_path.parent))
        try:
            shutil.copytree(source_path, staging, dirs_exist_ok=True)
            if target_path.exists():
                shutil.rmtree(target_path)
            os.replace(staging, target_path)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    TaskClient._copy_task_source_to_target = publish


def main():
    install_download_lock()
    from harbor.cli.main import app
    app()


if __name__ == '__main__':
    main()
