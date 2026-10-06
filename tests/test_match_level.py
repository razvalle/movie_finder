"""Boundary tests for full/partial match classification using synthetic records."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.smart_search import _basic_assessment


def movie(**overrides):
    record = {
        "id": 1, "title": "Synthetic Alpha", "synopsis": "A quiet harbor town.",
        "genres": ["drama"], "release_year": 1995, "themes": [], "keywords": [],
        "content_descriptors": [],
    }
    record.update(overrides)
    return record


def understanding(**overrides):
    value = {
        "core_intent": "", "hard_constraints": [], "keywords": [], "expanded_concepts": [],
        "example_titles": [], "similar_to": None,
        "filters": {"genres": [], "year_range": {"from": None, "to": None}},
    }
    value.update(overrides)
    return value


def assess(record, **overrides):
    return _basic_assessment("zzz", {"movie": record}, understanding(**overrides))


class MatchLevelTests(unittest.TestCase):
    def test_all_checked_constraints_satisfied_is_full(self):
        result = assess(movie(), filters={"genres": ["drama"], "year_range": {"from": 1990, "to": 1999}})
        self.assertEqual(result["match_level"], "full")

    def test_unsatisfied_constraint_is_partial(self):
        result = assess(movie(), filters={"genres": ["drama"], "year_range": {"from": 2000, "to": 2005}})
        self.assertEqual(result["match_level"], "partial")

    def test_unverifiable_constraint_is_partial(self):
        result = assess(
            movie(), hard_constraints=["exclude comedy"],
            filters={"genres": ["drama"], "year_range": {"from": None, "to": None}},
        )
        self.assertIn("absence of comedy", result["unverifiable"])
        self.assertEqual(result["match_level"], "partial")

    def test_no_constraints_checked_is_partial(self):
        self.assertEqual(assess(movie())["match_level"], "partial")

    def test_overlap_alone_does_not_make_exact(self):
        result = _basic_assessment(
            "harbor", {"movie": movie()}, understanding(keywords=["harbor"]),
        )
        self.assertEqual(result["match_level"], "partial")

    def test_excluded_genre_present_is_partial(self):
        result = assess(movie(genres=["drama", "comedy"]), hard_constraints=["exclude comedy"])
        self.assertEqual(result["match_level"], "partial")


if __name__ == "__main__":
    unittest.main()
