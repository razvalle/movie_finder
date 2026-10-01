"""Unit tests for query expansion, schema validation, and hybrid retrieval."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.generated_movies import GENERATED_MOVIES
from nlp.smart_search import _query_terms, local_understanding, rerank_candidates, retrieve_candidates, sanitize_understanding


class SmartSearchTests(unittest.TestCase):
    def test_dwarf_query_expands_to_fantasy_and_title_hints(self):
        query = local_understanding("recommend me a movie with dwarfs")
        self.assertIn("dwarf", query["keywords"])
        self.assertIn("fantasy", query["expanded_concepts"])
        self.assertIn("The Lord of the Rings: The Fellowship of the Ring", query["example_titles"])

    def test_decade_and_genre_are_extracted(self):
        query = local_understanding("90s comedy with a road trip")
        self.assertIn("comedy", query["genres"])
        self.assertEqual(query["year_range"], {"from": 1990, "to": 1999})

    def test_misspelled_dragon_query_has_retrieval_hints(self):
        query = local_understanding("moive with drgons")
        self.assertIn("dragon", query["keywords"])
        candidates = retrieve_candidates("moive with drgons", query, GENERATED_MOVIES, limit=5)
        titles = [item["movie"]["title"] for item in candidates]
        self.assertTrue(any("Dragon" in title or "Hobbit" in title for title in titles))

    def test_invalid_model_output_uses_local_schema_defaults(self):
        query = sanitize_understanding({"year_range": "invalid", "genres": [None], "min_rating": 99}, "dwarfs")
        self.assertEqual(query["year_range"], {"from": None, "to": None})
        self.assertEqual(query["genres"], [])
        self.assertIsNone(query["min_rating"])
        self.assertEqual(query["language_detected"], "en")

    def test_negated_filler_is_not_a_positive_retrieval_term(self):
        query = "a movie where there is no woman"
        understanding = local_understanding(query)
        terms = _query_terms(query, understanding)
        self.assertNotIn("there", terms)
        self.assertNotIn("woman", terms)
        self.assertEqual(retrieve_candidates(query, understanding, GENERATED_MOVIES), [])

    def test_negative_twist_is_partial_and_not_a_positive_term(self):
        query = "film where the ending is not a twist"
        understanding = local_understanding(query)
        terms = _query_terms(query, understanding)
        self.assertNotIn("ending", terms)
        self.assertNotIn("twist", terms)
        self.assertEqual(understanding["verifiability"], "partially_verifiable")
        self.assertIn("ending is not a twist", understanding["hard_constraints"])

    def test_animal_silent_black_and_white_constraints_are_preserved(self):
        understanding = local_understanding("a silent black-and-white movie from 2023 with only animals")
        self.assertTrue({"only animals", "silent film", "black-and-white"}.issubset(
            set(understanding["hard_constraints"])
        ))
        self.assertEqual(understanding["filters"]["year_range"], {"from": 2023, "to": 2023})

    def test_verification_failure_marks_results_partial(self):
        movie = GENERATED_MOVIES[0]
        candidate = {"movie": movie, "search_score": 90, "match_reason": "old"}
        query = "movie with dwarfs"
        with patch("nlp.smart_search._chat_json", return_value=None):
            results = rerank_candidates(query, [candidate], local_understanding(query))
        self.assertEqual(results[0]["match_level"], "partial")
        self.assertTrue(results[0]["verification_fallback"])
        self.assertNotIn("Related to", results[0]["match_reason"])

    def test_incomplete_verifier_response_fails_back_to_partial(self):
        movie = GENERATED_MOVIES[0]
        candidate = {"movie": movie, "search_score": 90, "match_reason": "old"}
        query = "query with one returned candidate"
        invalid = {"results": [{"id": "not-this-movie", "match_level": "full"}]}
        with patch("nlp.smart_search._chat_json", return_value=invalid):
            results = rerank_candidates(query, [candidate], local_understanding(query))
        self.assertEqual(results[0]["match_level"], "partial")
        self.assertTrue(results[0]["verification_fallback"])

    def test_valid_verifier_response_can_confirm_exact_match(self):
        movie = GENERATED_MOVIES[0]
        candidate = {"movie": movie, "search_score": 90, "match_reason": "old"}
        query = movie["title"]
        response = {"results": [{
            "id": movie["id"], "match_level": "full", "satisfied": [], "unsatisfied": [],
            "unverifiable": [], "confidence": 95, "reason": "The title exactly matches the requested movie.",
        }]}
        with patch("nlp.smart_search._chat_json", return_value=response):
            results = rerank_candidates(query, [candidate], local_understanding(query))
        self.assertEqual(results[0]["match_level"], "full")
        self.assertFalse(results[0]["verification_fallback"])


if __name__ == "__main__":
    unittest.main()
