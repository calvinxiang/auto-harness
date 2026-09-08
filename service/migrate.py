from pathlib import Path
from .db import connect


def migrate():
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(76412901)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
        for path in sorted(Path(__file__).with_name('migrations').glob('*.sql')):
            if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=%s', (path.name,)).fetchone():
                conn.execute(path.read_text())
                conn.execute('INSERT INTO schema_migrations(version) VALUES (%s)', (path.name,))


if __name__ == '__main__':
    migrate()
    print('Database migrations applied.')
