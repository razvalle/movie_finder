"""Focused tests for data-driven human-query normalization."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.movies import MOVIES
from nlp.human_input import (
    load_everyday_concepts,
    load_human_input_rules,
    match_everyday_concepts,
    normalize_human_input,
)
from nlp.catalog_matching import resolve_like_title
from nlp.normalize import normalize_text
from nlp.query import build_structured_query
from nlp.smart_search import interpret_query, retrieve_candidates


class HumanInputTests(unittest.TestCase):
    def test_unicode_and_noise_repair_preserves_the_intent(self):
        plain = normalize_human_input("horror movies", MOVIES)["text"]
        noisy = normalize_human_input(
            "ＨＯＲＲＯＲ\u00a0\u200bMOVIES…", MOVIES,
        )
        self.assertEqual(normalize_text(plain), normalize_text(noisy["text"]))
        self.assertIn("Unicode compatibility normalized", noisy["repairs"])
        self.assertIn("nonstandard whitespace normalized", noisy["repairs"])
        self.assertIn("invisible control characters removed", noisy["repairs"])

    def test_slang_emoji_hashtag_elongation_and_joined_words(self):
        rules = load_human_input_rules()
        self.assertTrue(rules["personas"])
        cases = {
            "pls recommend horrorr movies": "horror movies",
            "#HorrorMovies": "horror movies",
            "horrormovies": "horror movies",
            "😂 comedy movies": "funny comedy movies",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                result = normalize_human_input(source, MOVIES)
                self.assertIn(expected, normalize_text(result["text"]))

    def test_title_words_are_protected_from_spelling_correction(self):
        synthetic_catalog = [{"title": "Hrror", "genres": [], "keywords": []}]
        result = normalize_human_input("hrror", synthetic_catalog)
        self.assertEqual(result["text"], "hrror")

    def test_numeric_query_values_are_not_spelling_corrected(self):
        query = "need an action movie under 77 minutes after 1978"
        normalized = normalize_human_input(query, MOVIES)["text"]
        self.assertIn("under 77 minutes", normalized)
        understanding = interpret_query(query)
        self.assertEqual(understanding["filters"]["runtime"]["max"], 76)
        self.assertEqual(understanding["year_range"]["from"], 1979)

    def test_entity_syntax_is_preserved_during_joined_word_repair(self):
        query = "movies starring a person"
        normalized = normalize_human_input(query, MOVIES)["text"]
        self.assertIn("starring", normalized)
        self.assertTrue(build_structured_query(normalized)["unsupported_filters"]["people"])

    def test_like_title_prefers_exact_article_variant(self):
        catalog = [
            {"id": 1, "title": "Suicide Squad"},
            {"id": 2, "title": "The Suicide Squad"},
        ]
        movie, _ = resolve_like_title("movies like The Suicide Squad", catalog)
        self.assertEqual(movie["id"], 2)

    def test_everyday_mapping_expands_fighting_to_catalog_concepts(self):
        understanding = interpret_query("recommend me fighting movies")
        self.assertTrue({"action", "war", "sport"}.issubset(
            set(understanding["expanded_concepts"])
        ))
        self.assertEqual(
            [item["id"] for item in understanding["everyday_concepts"]],
            ["physical-conflict"],
        )
        results = retrieve_candidates(
            "recommend me fighting movies", understanding, MOVIES,
        )
        self.assertTrue(results)

    def test_mood_synonyms_drive_catalog_retrieval(self):
        query = "something mind blowing"
        understanding = interpret_query(query)
        results = retrieve_candidates(query, understanding, MOVIES)
        self.assertIn("thought-provoking", understanding["expanded_concepts"])
        self.assertTrue(results)
        self.assertTrue(any(
            "thought-provoking" in row["movie"].get("mood_tags", [])
            for row in results
        ))

    def test_negated_everyday_phrase_is_not_added_as_positive_expansion(self):
        understanding = interpret_query("not fighting movies")
        self.assertFalse(understanding["everyday_concepts"])
        self.assertFalse(
            {"action", "war", "sport"} & set(understanding["expanded_concepts"])
        )
        self.assertIn("fighting", understanding["negative_requirements"])

    def test_persona_and_behavior_templates_are_data_driven(self):
        rules = load_human_input_rules()
        self.assertGreaterEqual(len(rules["personas"]), 10)
        self.assertEqual(
            set(rules["noise_levels"]),
            {"none", "light", "medium", "heavy"},
        )
        self.assertTrue({
            "genre", "mood", "similarity", "runtime-year", "negation",
        }.issubset(rules["behavior_templates"]))
        self.assertTrue(load_everyday_concepts())
        self.assertIn("physical-conflict", {
            item["id"] for item in match_everyday_concepts("fighting")
        })

    def test_concept_expansions_have_catalog_evidence(self):
        searchable_fields = (
            "title", "original_title", "synopsis", "overview", "tagline",
            "genres", "themes", "mood_tags", "keywords", "content_descriptors",
        )
        catalog_text = []
        for movie in MOVIES:
            values = []
            for field in searchable_fields:
                value = movie.get(field)
                values.extend([value] if isinstance(value, str) else value or [])
            catalog_text.append(f" {normalize_text(' '.join(map(str, values)))} ")
        for entry in load_everyday_concepts():
            for term in entry["expanded_concepts"]:
                needle = f" {normalize_text(term)} "
                with self.subTest(concept=entry["id"], term=term):
                    self.assertTrue(any(needle in text for text in catalog_text))


if __name__ == "__main__":
    unittest.main()
