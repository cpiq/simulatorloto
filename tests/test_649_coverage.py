"""Offline regression checks for the recovered 649-coverage-v1 Python engine."""
from datetime import date, timedelta
import random
import unittest

from perplexitymix import (
    ENGINE_VERSION, MAX_SHARED, analyse649, generate649,
    evaluate649, normalize_draws, common,
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
        self.assertEqual(ENGINE_VERSION, "649-coverage-v1")

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
        self.assertEqual(result, {"hits": [3, 0], "best_hits": 3, "total_hits": 3})

    def test_coverage_report(self):
        result = generate649(self.model)
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


if __name__ == "__main__":
    unittest.main()
