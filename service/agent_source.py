"""Parse and package proposed Python as data. Never import or execute it here."""
import ast
import difflib
import hashlib
from pathlib import Path

from .tasks import TASKS


class AgentValidationError(ValueError):
    pass


def baseline_prompt():
    path = Path(__file__).resolve().parents[1] / 'agent/templates/terminal_bench.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'AGENT_INSTRUCTION' for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError('Baseline agent prompt missing')


def baseline_code(prompt=None):
    code = Path(__file__).with_name('agent_policy.py').read_text(encoding='utf-8')
    return code.replace("AGENT_INSTRUCTION = '__SYSTEM_PROMPT__'",
                        'AGENT_INSTRUCTION = ' + repr(baseline_prompt() if prompt is None else prompt), 1)


def validate_code(code):
    if len(code.encode()) > 60000:
        raise AgentValidationError('Agent module exceeds 60000 bytes')
    try:
        tree = ast.parse(code)
        compile(tree, '<candidate>', 'exec')  # Compile only; no module import or exec.
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise AgentValidationError('Invalid Python source: ' + type(exc).__name__) from None
    entries = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_agent']
    if len(entries) != 1 or entries[0].decorator_list or [a.arg for a in entries[0].args.args] != ['api', 'instruction']:
        raise AgentValidationError('Define run_agent(api, instruction) without decorators')
    prompts = [n for n in tree.body if isinstance(n, ast.Assign) and
               any(isinstance(t, ast.Name) and t.id == 'AGENT_INSTRUCTION' for t in n.targets)]
    if len(prompts) != 1 or not isinstance(prompts[0].value, ast.Constant) or not isinstance(prompts[0].value.value, str):
        raise AgentValidationError('AGENT_INSTRUCTION must be one literal string')
    # Review guardrails, not a Python security sandbox. Actual execution is isolated.
    allowed_imports = {'json', 're', 'math', 'collections', 'itertools', 'functools',
                       'datetime', 'time', 'shlex', 'textwrap', 'hashlib', 'statistics',
                       'copy', 'typing', 'dataclasses', 'pathlib', 'difflib', 'string'}
    forbidden = [*TASKS, '/tests/', '/solution/', '/logs/verifier', 'reward.txt',
                 'OPENAI_API_KEY', 'DATABASE_URL', 'BOOTSTRAP_TOKEN', 'E2B_API_KEY']
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
            if getattr(node, 'level', 0) or any(n.split('.')[0] not in allowed_imports for n in names):
                raise AgentValidationError('Use supported standard-library imports and api.model/api.bash')
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(value in node.value for value in forbidden):
                raise AgentValidationError('Agent source contains a benchmark identifier, protected path or credential name')
    return prompts[0].value.value


def render_code(code):
    source = Path(__file__).with_name('runtime.py').read_text(encoding='utf-8')
    source = source.replace("POLICY_SOURCE = '__AGENT_CODE__'", 'POLICY_SOURCE = ' + repr(code), 1)
    return source, hashlib.sha256(source.encode()).hexdigest()


def render(prompt):
    """Baseline convenience used by smoke tests and older callers."""
    return render_code(baseline_code(prompt))


def source_diff(before, after):
    return ''.join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                       fromfile='previous/agent.py', tofile='candidate/agent.py'))
