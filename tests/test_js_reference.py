"""Golden results from the published JavaScript engine, Sites version 25."""
import json
from pathlib import Path
import unittest

from perplexitymix import ENGINE_VERSION, generate649


class JavaScriptParityTests(unittest.TestCase):
    def test_exact_tickets_extras_and_metadata_match_published_javascript(self):
        reference = json.loads((Path(__file__).parent / "fixtures/649-coverage-v2-reference.json").read_text())
        self.assertEqual(reference["engine"], ENGINE_VERSION)
        for case in reference["cases"]:
            options = case["input"]
            with self.subTest(options=options):
                actual = generate649({"cycle_date": options["cycle_date"]},
                                     n=options["n"], seed=options["seed"], extra9=options["extra9"])
                self.assertEqual(actual, case["expected"])


if __name__ == "__main__":
    unittest.main()
