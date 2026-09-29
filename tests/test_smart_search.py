"""Unit tests for query expansion, schema validation, and hybrid retrieval."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.generated_movies import GENERATED_MOVIES
from nlp.smart_search import local_understanding, retrieve_candidates, sanitize_understanding


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


if __name__ == "__main__":
    unittest.main()
