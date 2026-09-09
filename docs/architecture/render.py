"""Extract and render the architecture document's named Mermaid diagrams."""
import argparse
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True,
                        help='Path to @mermaid-js/mermaid-cli/src/cli.js (11.12.0)')
    parser.add_argument('--puppeteer-config', type=Path,
                        help='Optional local JSON config selecting a browser executable')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    document = (root.parent / 'ARCHITECTURE.md').read_text(encoding='utf-8')
    diagrams = re.findall(
        r'<!-- diagram: ([a-z-]+) -->\s*```mermaid\n(.*?)\n```', document, re.S)
    expected = ['deployment', 'execution-recovery', 'data-ownership', 'package-search']
    if [name for name, _ in diagrams] != expected:
        parser.error('Expected the four named architecture diagrams in document order')
    cli = args.cli.resolve(strict=True)
    for name, source in diagrams:
        path = root / (name + '.mmd')
        path.write_text(source + '\n', encoding='utf-8', newline='\n')
        command = ['node', str(cli), '--input', str(path),
                   '--output', str(root / (name + '.svg')),
                   '--configFile', str(root / 'mermaid-config.json'),
                   '--backgroundColor', 'white']
        if args.puppeteer_config:
            command.extend(['--puppeteerConfigFile', str(args.puppeteer_config.resolve(strict=True))])
        subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
