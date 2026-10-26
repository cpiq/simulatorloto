"""Bundle the shared archive parser into a single downloadable Python script."""
from pathlib import Path
import argparse
import re

ROOT = Path(__file__).resolve().parents[1]


def standalone_source():
    engine = (ROOT / 'perplexitymix.py').read_text(encoding='utf-8')
    parser = (ROOT / 'loto_archive_parser.py').read_text(encoding='utf-8')
    source, count = re.subn(r'from loto_archive_parser import \([\s\S]*?\n\)',
                            lambda _match: '# BEGIN bundled archive parser\n' + parser.rstrip() + '\n# END bundled archive parser',
                            engine, count=1)
    if count != 1:
        raise RuntimeError('Expected exactly one shared parser import.')
    source = source.replace('pip install requests beautifulsoup4 tzdata', 'pip install requests tzdata')
    source = source.replace('Rulare: python perplexitymix.py', 'Rulare: python loto649_standalone.py')
    compile(source, 'loto649_standalone.py', 'exec')
    return source


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, default=ROOT / 'loto649_standalone.py')
    options = cli.parse_args()
    options.output.write_text(standalone_source(), encoding='utf-8')
    print(options.output)
