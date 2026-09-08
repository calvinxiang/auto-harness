import ast
import hashlib
from pathlib import Path


def baseline_prompt():
    path = Path(__file__).resolve().parents[1] / 'agent/templates/terminal_bench.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'AGENT_INSTRUCTION' for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError('Baseline agent prompt missing')


def render(prompt):
    source = Path(__file__).with_name('runtime.py').read_text(encoding='utf-8')
    source = source.replace("AGENT_INSTRUCTION = '__SYSTEM_PROMPT__'", 'AGENT_INSTRUCTION = ' + repr(prompt), 1)
    ast.parse(source)  # Parsing only; proposed content is never executed by the worker.
    return source, hashlib.sha256(source.encode()).hexdigest()
