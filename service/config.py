from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql://harness:local-development-only@localhost:5432/harness'
    bootstrap_token: str = ''
    openai_api_key: str = ''
    openai_base_url: str = 'https://api.openai.com/v1'
    agent_model: str = 'gpt-4.1-mini'
    optimizer_model: str = 'gpt-4.1-mini'
    sandbox_provider: str = 'docker'
    artifacts_dir: str = '/artifacts'
    lease_seconds: int = 90
    heartbeat_seconds: int = 10
    max_attempts: int = 3
    benchmark_timeout_seconds: int = 3600


@lru_cache
def settings() -> Settings:
    return Settings()


def execution_config():
    cfg = settings()
    return {'dataset': 'terminal-bench@2.0', 'agent_model': cfg.agent_model,
            'optimizer_model': cfg.optimizer_model, 'sandbox_provider': cfg.sandbox_provider,
            'agent_timeout_seconds': 300, 'max_steps': 80, 'task_concurrency': 2,
            'harbor_version': '0.1.45', 'agent_contract': 'python-policy-v1'}
