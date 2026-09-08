"""Initialize local settings without overwriting existing credentials. Stdlib only."""
from pathlib import Path
import secrets


def main():
    path = Path('.env')
    if not path.exists():
        path.write_text(Path('.env.example').read_text(encoding='utf-8'))
    content = path.read_text(encoding='utf-8-sig')
    lines = content.splitlines()
    existing = next((line for line in lines if line.startswith('BOOTSTRAP_TOKEN=')), None)
    if existing is None:
        lines.append('BOOTSTRAP_TOKEN=' + secrets.token_urlsafe(32))
    elif not existing.partition('=')[2].strip():
        lines[lines.index(existing)] = 'BOOTSTRAP_TOKEN=' + secrets.token_urlsafe(32)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Local .env initialized. Set OPENAI_API_KEY before starting the worker. Credential values are hidden.')


if __name__ == '__main__':
    main()
