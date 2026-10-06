"""Unit tests for the benchmark generator and scorer (no search pipeline involved)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.relevance_benchmark import generate_queries, score, split_of, violates


def catalog(n=200):
    return [
        {"id": i, "title": f"Harbor{chr(97 + i % 26)}ville Story", "synopsis": "", "keywords": [],
         "genres": ["drama", "comedy"] if i % 2 else ["horror"], "release_year": 1980 + i % 40,
         "runtime": 80 + i % 60}
        for i in range(1, n + 1)
    ]


class BenchmarkGeneratorTests(unittest.TestCase):
    def test_splits_are_disjoint_and_deterministic(self):
        movies = catalog()
        tune = generate_queries(movies, "tune", count=30)
        heldout = generate_queries(movies, "heldout", count=30)
        self.assertEqual(tune, generate_queries(movies, "tune", count=30))
        self.assertFalse({q["source_id"] for q in tune} & {q["source_id"] for q in heldout})
        self.assertTrue(all(split_of(next(m for m in movies if m["id"] == q["source_id"])) == "tune" for q in tune))

    def test_source_movie_satisfies_its_own_constraints(self):
        movies = {m["id"]: m for m in catalog()}
        for q in generate_queries(list(movies.values()), "heldout", count=40):
            self.assertFalse(violates(movies[q["source_id"]], q["constraints"]), q)

    def test_score_metrics(self):
        movies = {m["id"]: m for m in catalog(10)}
        q = {"query": "x", "source_id": 2, "constraints": {"genres": ["horror"]}}
        metrics = score([q], {"x": [(1, True), (2, False)]}, movies)
        self.assertEqual(metrics["recall@1"], 0.0)
        self.assertEqual(metrics["recall@5"], 1.0)
        self.assertEqual(metrics["hard_constraint_violation_rate"], 0.5)
        self.assertEqual(metrics["verified_hard_constraint_violation_rate"], 0.0)
        self.assertEqual(metrics["unverified_result_rate"], 0.5)
        self.assertEqual(metrics["unverified_hard_constraint_violation_rate"], 1.0)
        self.assertEqual(score([q], {"x": []}, movies)["empty_result_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
