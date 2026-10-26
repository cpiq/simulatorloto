"""Offline regression checks for the 649-coverage-v2 Python engine."""
from datetime import date, timedelta
import random
import unittest

from perplexitymix import (
    ENGINE_VERSION, MAX_SHARED, analyse649, generate649,
    evaluate649, normalize_draws, common, backtest649, evaluateSystem649,
)


class CoverageEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(20261026)
        first = date(2020, 1, 1)
        cls.draws = normalize_draws([
            {"date": (first + timedelta(days=i)).isoformat(),
             "numbers": sorted(rng.sample(range(1, 50), 6))}
            for i in range(330)
        ])
        cls.model = analyse649(cls.draws, fixed_config={
            "frequencyInfluence": 0, "pairInfluence": 0,
            "delayInfluence": 0, "filter": False,
        })

    def test_engine_version(self):
        self.assertEqual(ENGINE_VERSION, "649-coverage-v2")

    def test_default_six_tickets_and_seventh_extra(self):
        result = generate649(self.model)
        tickets, extensions = result["tickets"], result["extensions"]
        self.assertEqual(len(tickets), 6)
        self.assertEqual(len(extensions), 6)
        for item, extension in zip(tickets, extensions):
            self.assertEqual(len(item["nums"]), 6)
            self.assertEqual(len(set(item["nums"])), 6)
            self.assertEqual(len(extension), 7)
            self.assertEqual(set(item["nums"]) - set(extension), set())
            self.assertTrue(all(1 <= n <= 49 for n in extension))
        for i in range(len(tickets)):
            for j in range(i + 1, len(tickets)):
                self.assertLessEqual(common(tickets[i]["nums"], tickets[j]["nums"]), MAX_SHARED)

    def test_reproducibility(self):
        self.assertEqual(generate649(self.model), generate649(self.model))

    def test_three_hits_are_detected(self):
        # A deliberately constructed ticket with exactly three matches.
        draw = {"date": "2026-10-25", "numbers": [10, 27, 28, 29, 33, 39]}
        result = evaluate649([[1, 2, 3, 10, 27, 28], [1, 2, 3, 4, 5, 6]], draw)
        self.assertEqual(result["hits"], [3, 0])
        self.assertEqual(result["best_hits"], 3)
        self.assertEqual(result["variants_ge3"], 1)
        self.assertEqual(result["variants_ge4"], 0)

    def test_coverage_report(self):
        result = generate649(self.model)
        self.assertEqual(result["coverage"]["distinct_numbers"], 36)
        self.assertEqual(result["coverage"]["max_overlap"], 0)
        self.assertEqual(result["coverage"]["unique_triples"], 120)
        self.assertLessEqual(result["coverage"]["max_overlap"], 2)
        self.assertEqual(result["coverage"]["distinct_numbers"], len(set(
            n for ticket in result["tickets"] for n in ticket["nums"]
        )))

    def test_no_duplicate_tickets_different_seeds(self):
        for seed in (0, 1, 649, 777, 20261026):
            result = generate649(self.model, 6, seed, 1)
            tickets = [tuple(t["nums"]) for t in result["tickets"]]
            self.assertEqual(len(tickets), len(set(tickets)))

    def test_old_history_and_calibration_cannot_change_current_cycle(self):
        expected = generate649(self.model)
        corrected = [{**row, "numbers": [1, 2, 3, 4, 5, 6]} for row in self.draws]
        config = {"frequencyInfluence": .5, "pairInfluence": .5,
                  "delayInfluence": .3, "filter": True}
        changed = analyse649(corrected, fixed_config=config)
        self.assertEqual(generate649(changed), expected)
        self.assertEqual(generate649({"cycle_date": self.draws[-1]["date"]}), expected)
        rotated = generate649({"cycle_date": "2026-10-25"})
        self.assertNotEqual(rotated["tickets"], expected["tickets"])

    def test_all_supported_counts_have_stable_prefixes_and_bounded_pairs(self):
        for seed in (0, 649, 4294967295):
            full = generate649(self.model, n=50, seed=seed)
            tickets = [row["nums"] for row in full["tickets"]]
            self.assertEqual(len(set(number for row in tickets[:8] for number in row)), 48)
            for index, row in enumerate(tickets):
                for other in tickets[index + 1:]:
                    self.assertLessEqual(common(row, other), 1)
            for n in (1, 6, 8, 9, 50):
                prefix = generate649(self.model, n=n, seed=seed)
                self.assertEqual(prefix["tickets"], full["tickets"][:n])
                self.assertEqual(prefix["extensions"], full["extensions"][:n])
            large = generate649(self.model, n=50, seed=seed, extra9=10)
            self.assertEqual(large["tickets"], full["tickets"])
            for first, second in zip(full["extensions"], large["extensions"]):
                self.assertTrue(set(first) <= set(second))

    def test_cumulative_matches_and_full_system_cost(self):
        draw = {"date": "2026-10-25", "numbers": [1, 2, 3, 4, 5, 6]}
        result = evaluate649([[1, 2, 3, 4, 8, 9], [1, 2, 3, 4, 5, 9]], draw)
        self.assertEqual(result["variants_ge4"], 2)
        self.assertEqual(result["variants_ge5"], 1)
        system = evaluateSystem649([[1, 2, 3, 4, 5, 8, 9]], draw)
        self.assertEqual(system["paid_variants"], 7)
        self.assertEqual(system["variants_ge4"], 7)
        self.assertEqual(system["variants_ge5"], 2)
        self.assertEqual(evaluate649([], draw)["best_hits"], 0)
        with self.assertRaises(ValueError):
            evaluate649([[1, 2, 3, 4, 5, 6]], {**draw, "joker": 9})

    def test_backtest_groups_same_day_and_uses_only_previous_day(self):
        training = self.draws[:260]
        targets = [{"date": "2026-10-22", "numbers": [4, 5, 7, 8, 17, 29]},
                   {"date": "2026-10-22", "numbers": [1, 2, 3, 4, 5, 6]}]
        result = backtest649(training + targets, window=1)
        pack = generate649({"cycle_date": training[-1]["date"]})
        self.assertEqual(result["n"], 6)
        self.assertEqual(result["extra9"], 1)
        self.assertEqual(len(result["rows"]), 2)
        for row in result["rows"]:
            self.assertEqual(row["training_cutoff"], training[-1]["date"])
            self.assertEqual(row["hits"], evaluate649([t["nums"] for t in pack["tickets"]], row)["hits"])


if __name__ == "__main__":
    unittest.main()
