"""Trusted Harbor bridge. All agent inference and bash execution run in the task sandbox."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from harbor.agents.base import BaseAgent


class SandboxHarnessAgent(BaseAgent):
    @staticmethod
    def name():
        return 'sandbox-harness-agent'

    def version(self):
        return '1.0.0'

    async def setup(self, environment):
        # Selected benchmark images are Debian/Ubuntu. Do not alter their task data.
        result = await environment.exec('command -v python3 >/dev/null || (apt-get update -qq && apt-get install -y -qq python3 ca-certificates)', timeout_sec=180)
        if result.return_code:
            raise RuntimeError('Could not install sandbox Python runtime')
        await environment.exec('mkdir -p /opt/harness /logs/agent', timeout_sec=10)
        await environment.upload_file(Path(os.environ['HARNESS_AGENT_SOURCE']), '/opt/harness/agent.py')

    async def run(self, instruction, environment, context):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'instruction.txt'
            path.write_text(instruction)
            await environment.upload_file(path, '/opt/harness/instruction.txt')
        # Only inference credentials cross the boundary: no DB, bootstrap or E2B key.
        env = {key: os.environ[key] for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'AGENT_MODEL')}
        for key in ('AGENT_API', 'AGENT_REASONING_EFFORT', 'AGENT_MAX_OUTPUT_TOKENS'):
            if key in os.environ:
                env[key] = os.environ[key]
        result = await environment.exec('timeout --signal=TERM --kill-after=5s 300s python3 /opt/harness/agent.py', env=env, timeout_sec=320)
        try:
            meta_path = self.logs_dir / 'meta.json'
            await environment.download_file('/logs/agent/meta.json', meta_path)
            meta = json.loads(meta_path.read_text())
            context.n_input_tokens = meta.get('input_tokens', 0)
            context.n_output_tokens = meta.get('output_tokens', 0)
        except (OSError, ValueError):
            pass
        if result.return_code not in (0, 124, 137):
            raise RuntimeError('Sandbox agent failed; inspect its captured trace and metadata')
