"""Validate and snapshot harness packages as data. Only sandboxes load their code."""
import ast
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from pydantic import Field, model_validator

from .agent_source import baseline_code, source_diff
from .schemas import Input
from .tasks import TASKS, HOLDOUT_TASKS


class Skill(Input):
    name: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    description: str = Field(min_length=1, max_length=500)
    path: str
    resources: list[str] = Field(default_factory=list, max_length=10)


class HarnessPackage(Input):
    entrypoint: str = 'agent.py'
    files: dict[str, str] = Field(min_length=1, max_length=32)
    skills: list[Skill] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def contents(self):
        paths = self.files
        if sum(len(s.encode()) for s in paths.values()) > 256000:
            raise ValueError('Package exceeds 256000 bytes')
        reserved = {'runtime', 'service', 'sitecustomize', 'usercustomize'}
        forbidden = [*TASKS, *HOLDOUT_TASKS, '/tests/', '/solution/', '/logs/verifier', 'reward.txt',
                     'contract-check', 'contract_fixture', 'FixtureAPI',
                     'OPENAI_API_KEY', 'DATABASE_URL', 'BOOTSTRAP_TOKEN', 'E2B_API_KEY']
        folded = set()
        for name, source in paths.items():
            p = PurePosixPath(name)
            if (len(name) > 180 or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)
                    or p.is_absolute() or any(part in ('', '.', '..') or part.startswith('.') for part in name.split('/'))
                    or p.suffix not in ('.py', '.md', '.json', '.sh', '.txt')
                    or p.stem in reserved or '\x00' in source or name.casefold() in folded):
                raise ValueError('Invalid, duplicate or reserved package path')
            folded.add(name.casefold())
            if any(value in source for value in forbidden):
                raise ValueError('Package contains a benchmark identifier, protected path or credential name')
            if p.suffix == '.py':
                try:
                    compile(source, name, 'exec')  # No import or execution in the service.
                except (SyntaxError, ValueError, RecursionError):
                    raise ValueError('Invalid Python package source') from None
            if p.suffix == '.json':
                try:
                    json.loads(source)
                except ValueError:
                    raise ValueError('Invalid JSON asset') from None
        if self.entrypoint not in paths or not self.entrypoint.endswith('.py'):
            raise ValueError('Entrypoint must name a Python file in the package')
        tree = ast.parse(paths[self.entrypoint])
        entries = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_agent']
        if len(entries) != 1 or entries[0].decorator_list or [a.arg for a in entries[0].args.args] != ['api', 'instruction']:
            raise ValueError('Entrypoint must define run_agent(api, instruction)')
        names = set()
        for skill in self.skills:
            if skill.name in names or skill.path not in paths or not skill.path.endswith('.md'):
                raise ValueError('Skills need unique names and a declared Markdown file')
            if any(resource not in paths for resource in skill.resources):
                raise ValueError('Skill resources must be declared package files')
            names.add(skill.name)
        # Reject file/directory collisions before ever materializing this package.
        if any(str(parent).casefold() in folded for name in paths for parent in PurePosixPath(name).parents):
            raise ValueError('Package has a file/directory collision')
        return self


def baseline_package():
    return HarnessPackage(files={'agent.py': baseline_code()}).model_dump()


def package_snapshot(value, parent=None):
    package = HarnessPackage.model_validate(value).model_dump()
    canonical = json.dumps(package, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    hashes = {name: hashlib.sha256(content.encode()).hexdigest() for name, content in package['files'].items()}
    runtime = Path(__file__).with_name('runtime.py').read_text(encoding='utf-8')
    source = runtime.replace("POLICY_SOURCE = '__AGENT_CODE__'", 'POLICY_SOURCE = ' + repr(package['files'][package['entrypoint']]), 1)
    source = source.replace('BUNDLE_FILES = {}', 'BUNDLE_FILES = ' + repr(package['files']), 1)
    source = source.replace("BUNDLE_ENTRYPOINT = 'agent.py'", 'BUNDLE_ENTRYPOINT = ' + repr(package['entrypoint']), 1)
    source = source.replace('BUNDLE_SKILLS = []', 'BUNDLE_SKILLS = ' + repr(package['skills']), 1)
    previous = (parent or {}).get('files', {})
    changes = {name: source_diff(previous.get(name, ''), package['files'].get(name, ''))
               for name in sorted(previous.keys() | package['files'].keys())
               if previous.get(name) != package['files'].get(name)}
    return {'package': package, 'package_sha256': hashlib.sha256(canonical.encode()).hexdigest(),
            'runtime_sha256': hashlib.sha256(runtime.encode()).hexdigest(),
            'file_hashes': hashes, 'agent_source': source,
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'changes': changes}
