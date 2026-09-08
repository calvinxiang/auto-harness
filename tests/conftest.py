import pytest
from service.config import settings
from service.db import connect
from service.migrate import migrate


@pytest.fixture(autouse=True)
def database(monkeypatch):
    if not settings().database_url.rsplit('/', 1)[-1].endswith('_test'):
        pytest.fail('Refusing to run destructive test isolation outside a *_test database')
    monkeypatch.setenv('BOOTSTRAP_TOKEN', 'integration-test-bootstrap')
    settings.cache_clear()
    migrate()
    with connect() as conn:
        conn.execute('TRUNCATE jobs,iterations,memberships,organizations,users CASCADE')
        conn.execute('UPDATE execution_pool SET capacity=8 WHERE id=1')
    yield
    settings.cache_clear()
