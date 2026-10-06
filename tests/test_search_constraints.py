"""Regression tests for explicit runtime limits and required content terms."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import movie_matches_runtime_limits
from nlp.smart_search import _basic_assessment, local_understanding, retrieve_candidates


def movie(movie_id, title, synopsis, runtime=90):
    return {
        "id": movie_id, "title": title, "synopsis": synopsis,
        "genres": ["drama"], "release_year": 2000, "runtime": runtime,
        "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
    }


class RuntimeConstraintTests(unittest.TestCase):
    def test_explicit_runtime_bounds_are_hard(self):
        preferences = {"runtime": {"min": None, "max": 100}}
        self.assertTrue(movie_matches_runtime_limits(movie(1, "Short", "A story.", 100), preferences))
        self.assertFalse(movie_matches_runtime_limits(movie(2, "Long", "A story.", 101), preferences))
        self.assertFalse(movie_matches_runtime_limits(movie(3, "Unknown", "A story.", 0), preferences))

    def test_around_runtime_remains_a_soft_preference(self):
        preferences = {"runtime": {"min": None, "max": None, "target": 100}}
        self.assertTrue(movie_matches_runtime_limits(movie(1, "Long", "A story.", 150), preferences))

    def test_shorter_is_not_a_required_content_term(self):
        parsed = local_understanding("a drama movie shorter than 120 minutes with harbor")
        self.assertEqual(parsed["required_terms"], ["harbor"])

    def test_runtime_violation_is_not_classified_as_full(self):
        parsed = local_understanding("drama under 100 minutes")
        result = _basic_assessment("drama under 100 minutes", {"movie": movie(2, "Long", "A drama.", 101)}, parsed)
        self.assertEqual(result["match_level"], "partial")
        self.assertIn("requested runtime", result["unsatisfied"])

    def test_unknown_runtime_is_not_classified_as_full(self):
        parsed = local_understanding("drama under 100 minutes")
        result = _basic_assessment("drama under 100 minutes", {"movie": movie(3, "Unknown", "A drama.", 0)}, parsed)
        self.assertEqual(result["match_level"], "partial")
        self.assertIn("requested runtime", result["unsatisfied"])


class RequiredKeywordTests(unittest.TestCase):
    def test_retrieval_rejects_partial_keyword_matches(self):
        records = [
            movie(1, "Harbor Lights", "A drama at a harbor."),
            movie(2, "City Stories", "A drama about a city."),
        ]
        query = "drama film about harbor"
        understanding = local_understanding(query)
        candidates = retrieve_candidates(query, understanding, records, limit=10)
        self.assertTrue(candidates)
        self.assertEqual([item["movie"]["id"] for item in candidates], [1])

    def test_missing_required_term_is_unverified_not_full(self):
        query = "drama film about harbor"
        understanding = local_understanding(query)
        result = _basic_assessment(query, {"movie": movie(2, "City Stories", "A drama about a city.")}, understanding)
        self.assertEqual(result["match_level"], "partial")
        self.assertIn("keyword harbor", result["unverifiable"])
        self.assertTrue(result["verification_fallback"])


if __name__ == "__main__":
    unittest.main()