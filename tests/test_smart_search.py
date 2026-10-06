"""Unit tests for query expansion, schema validation, and hybrid retrieval."""

import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.generated_movies import GENERATED_MOVIES
from nlp.smart_search import _query_terms, _similar_movie_terms, local_understanding, rerank_candidates, retrieve_candidates, sanitize_understanding, interpret_query


class SmartSearchTests(unittest.TestCase):
    def test_dwarf_query_expands_to_fantasy_and_title_hints(self):
        query = local_understanding("recommend me a movie with dwarfs")
        self.assertIn("dwarf", query["keywords"])
        self.assertIn("fantasy", query["expanded_concepts"])
        self.assertEqual(query["example_titles"], [])

    def test_similar_to_title_comes_from_the_query_text(self):
        self.assertEqual(local_understanding("something like Heat but shorter")["similar_to"], "heat")
        self.assertIsNone(local_understanding("a funny movie")["similar_to"])

    def test_similar_to_expands_from_catalog_record_only_when_title_exists(self):
        catalog = [{"id": 1, "title": "Zorbo", "genres": ["thriller"], "themes": ["heist"], "keywords": ["Zorbo", "vault"]}]
        self.assertEqual(_similar_movie_terms({"similar_to": "zorbo"}, catalog), ["thriller", "heist", "vault"])
        self.assertEqual(_similar_movie_terms({"similar_to": "missing title"}, catalog), [])
        self.assertEqual(_similar_movie_terms({"similar_to": None}, catalog), [])

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

    def test_core_search_is_rule_based_and_explainable_without_network(self):
        query = "moive with drgons"
        with patch.dict(os.environ, {}, clear=True), patch(
            "socket.create_connection",
            side_effect=AssertionError("core search must not make network requests"),
        ):
            understanding = interpret_query(query)
            candidates = retrieve_candidates(query, understanding, GENERATED_MOVIES, limit=5)
            results = rerank_candidates(query, candidates, understanding)

        self.assertIn("dragon", understanding["keywords"])
        self.assertTrue(candidates)
        self.assertTrue(any("Dragon" in item["movie"]["title"] for item in candidates))
        self.assertTrue(results)
        self.assertTrue(all(item["match_reason"] for item in results))
        self.assertTrue(all(not item["verification_fallback"] for item in results))


if __name__ == "__main__":
    unittest.main()
