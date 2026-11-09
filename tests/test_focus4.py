"""Regression tests for the explicit four-hit coverage options."""
from datetime import date, timedelta
from pathlib import Path
import json
import math
import subprocess
import sys
import tempfile
import unittest

from perplexitymix import generate649

ROOT = Path(__file__).resolve().parents[1]


class FourHitCoverageTests(unittest.TestCase):
    def test_opt_in_six_full_seven_number_systems_are_disjoint(self):
        for seed in (0, 649, 20261109, 4294967295):
            for n in (1, 3, 6):
                model = {"cycle_date": "2026-11-08"}
                old = generate649(model, n=n, seed=seed)
                new = generate649(model, n=n, seed=seed, extension_strategy="disjoint7")
                self.assertEqual(old["tickets"], new["tickets"])
                self.assertEqual(new, generate649(model, n=n, seed=seed, extension_strategy="disjoint7"))
                extensions = new["extensions"]
                self.assertEqual(len(extensions), n)
                self.assertEqual(len(set(v for ext in extensions for v in ext)), n*7)
                self.assertTrue(all(len(ext) == 7 for ext in extensions))
                self.assertEqual(new["coverage"]["paid_simple_variants"], 7*n)
                self.assertEqual(new["coverage"]["disjoint7_distinct_numbers"], 7*n)
                self.assertFalse(new["coverage"]["guaranteed_ge4"])

    def test_42_simple_bets_have_pairwise_overlap_at_most_one(self):
        for seed in (0, 649, 20261109):
            pack = generate649({"cycle_date": "2026-11-08"}, n=42, seed=seed, extra9=0)
            tickets = [set(t["nums"]) for t in pack["tickets"]]
            self.assertEqual(len(tickets), 42)
            self.assertTrue(all(len(a & b) <= 1 for i,a in enumerate(tickets)
                                for b in tickets[i+1:]))

    def test_disjoint7_probability_and_equal_budget_comparison(self):
        pack = generate649({"cycle_date": "2026-11-08"}, n=6, seed=649,
                           extra9=1, extension_strategy="disjoint7")
        exact = math.comb(49,6)
        p_simple = sum(math.comb(6,k)*math.comb(43,6-k) for k in (4,5,6))/exact
        p_seven = sum(math.comb(7,k)*math.comb(42,6-k) for k in (4,5,6))/exact
        c = pack["coverage"]
        self.assertAlmostEqual(c["probability_ge4_disjoint7"], 6*p_seven)
        self.assertAlmostEqual(c["probability_ge4_equal_budget_simple"], 42*p_simple)
        self.assertGreater(42*p_simple, 6*p_seven)

    def test_reject_invalid_disjoint7_combinations(self):
        model = {"cycle_date": "2026-11-08"}
        for kwargs in ({"n":7,"extra9":1}, {"n":6,"extra9":0},
                       {"n":6,"extra9":2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                generate649(model, extension_strategy="disjoint7", **kwargs)
        with self.assertRaises(ValueError):
            generate649(model, extension_strategy="not_a_strategy")

    def test_vscode_standalone_json_export_and_flags(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            history = [{"date": (date(2025,1,1)+timedelta(days=i)).isoformat(),
                        "numbers": [1,2,3,4,5,6]} for i in range(30)]
            h = folder/"history.json"
            h.write_text(json.dumps(history),encoding="utf-8")
            script = ROOT/"loto649_standalone.py"
            modes = [
                (["--focus4"], 42, 0, "independent"),
                (["--n","6","--extra","1","--extension-strategy","disjoint7"],6,1,"disjoint7"),
            ]
            for i,(flags,n,extra,strategy) in enumerate(modes):
                output = folder/f"results_{i}.json"
                result = subprocess.run([sys.executable,str(script),"--history-json",str(h),
                                         "--json",str(output),*flags],cwd=folder,
                                        capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode, 0, result.stdout+"\n"+result.stderr)
                data = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(len(data["tickets"]),n)
                self.assertEqual(data["parameters"]["extra9"],extra)
                self.assertEqual(data["parameters"]["extension_strategy"],strategy)


if __name__ == "__main__":
    unittest.main()
