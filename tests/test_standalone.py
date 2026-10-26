from datetime import date, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from perplexitymix import generate649

ROOT = Path(__file__).resolve().parents[1]


class StandaloneTests(unittest.TestCase):
    def test_bundle_matches_shared_sources_and_runs_in_an_empty_directory(self):
        spec = importlib.util.spec_from_file_location('bundle_builder', ROOT / 'tools/build_standalone.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        bundled = (ROOT / 'loto649_standalone.py').read_text(encoding='utf-8')
        self.assertEqual(bundled, builder.standalone_source())
        self.assertNotIn('from loto_archive_parser import', bundled)
        history = [{'date': (date(2026, 9, 26) + timedelta(days=i)).isoformat(),
                    'numbers': [1, 2, 3, 4, 5, 6]} for i in range(30)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'loto649_standalone.py').write_text(bundled, encoding='utf-8')
            (path / 'history.json').write_text(json.dumps(history))
            result = subprocess.run([sys.executable, 'loto649_standalone.py', '--history-json', 'history.json', '--json', 'result.json'],
                                    cwd=folder, env={**os.environ, 'PYTHONPATH': ''},
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = json.loads((path / 'result.json').read_text())
        expected = generate649({'cycle_date': '2026-10-25'})
        for key in ['tickets', 'extensions', 'generation', 'coverage']:
            self.assertEqual(actual[key], expected[key])


if __name__ == '__main__':
    unittest.main()
