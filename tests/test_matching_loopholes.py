import re
import unittest

from data.movies import MOVIES
from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)
from nlp.extract import (
    _catalog_ambiguous_terms,
    _high_entropy_terms,
    extract_preferences,
)
from nlp.fuzzy import correct_unknown_token
from nlp.scoring import is_excluded, overlap_fraction
from nlp.smart_search import _query_evidence, local_understanding


def _catalog_words(movies):
    words = set()
    for movie in movies:
        for field in (
            "title", "original_title", "synopsis", "overview", "tagline",
            "genres", "themes", "mood_tags", "keywords", "content_descriptors",
        ):
            value = movie.get(field)
            values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            for item in values:
                if isinstance(item, str):
                    words.update(re.findall(r"[a-z0-9]+", item.casefold()))
    return words


def _synonym_words():
    words = set()
    for table in (
        GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS,
        CONTENT_DESCRIPTOR_SYNONYMS,
    ):
        for canonical, forms in table.items():
            for phrase in {canonical, *forms}:
                words.update(re.findall(r"[a-z0-9]+", phrase.casefold()))
    return words


def _contains_token_sequence(text, phrase):
    text_tokens = re.findall(r"[a-z0-9]+", str(text).casefold())
    phrase_tokens = re.findall(r"[a-z0-9]+", str(phrase).casefold())
    return bool(phrase_tokens) and any(
        text_tokens[start:start + len(phrase_tokens)] == phrase_tokens
        for start in range(len(text_tokens) - len(phrase_tokens) + 1)
    )


def _is_inflectional_plural(word, candidate):
    if len(candidate) > 4 and candidate.endswith("ies") and candidate[:-3] + "y" == word:
        return True
    if len(candidate) > 4 and candidate.endswith(("ches", "shes", "sses", "xes", "zes")):
        return candidate[:-2] == word
    return len(candidate) > 3 and candidate.endswith("s") and candidate[:-1] == word


class WholeWordMatchingTests(unittest.TestCase):
    def test_substrings_in_catalog_tokens_are_not_matched(self):
        catalog_words = _catalog_words(MOVIES)
        vocabulary = catalog_words | _synonym_words()
        pairs = [
            (word, next(
                (candidate for candidate in sorted(catalog_words)
                 if candidate != word and word in candidate
                 and not _contains_token_sequence(candidate, word)
                 and not _is_inflectional_plural(word, candidate)),
                None,
            ))
            for word in sorted(vocabulary)
        ]
        pairs = [(word, longer) for word, longer in pairs if longer]
        self.assertTrue(pairs, "The catalog should provide substring-collision cases.")

        for word, longer in pairs:
            with self.subTest(word=word, catalog_word=longer):
                movie = {
                    "id": "synthetic",
                    "title": "Synthetic record",
                    "synopsis": longer,
                    "genres": [longer],
                    "mood_tags": [],
                    "themes": [],
                    "keywords": [longer],
                    "content_descriptors": [],
                }
                self.assertFalse(_contains_token_sequence(longer, word))
                self.assertFalse(is_excluded(movie, {"excluded_keywords": [word]}))
                self.assertEqual(overlap_fraction([word], [longer])["fraction"], 0)
                self.assertEqual(_query_evidence(movie, [[word]])["details"], [])

    def test_in_vocabulary_substring_is_not_fuzzily_reinterpreted(self):
        preferences = extract_preferences("not warm")
        self.assertNotIn("war", preferences["excluded_genres"])
        self.assertIn("warm", preferences["excluded_keywords"])
        self.assertFalse(
            is_excluded({"title": "Synthetic", "genres": ["war"]}, preferences)
        )


class SafeFuzzyCorrectionTests(unittest.TestCase):
    def test_all_catalog_and_synonym_tokens_are_protected(self):
        vocabulary = _catalog_words(MOVIES) | _synonym_words()
        unchanged = [
            word for word in vocabulary
            if correct_unknown_token(word, vocabulary) is not None
        ]
        self.assertEqual(unchanged, [])

    def test_unknown_typo_requires_a_unique_nearest_candidate(self):
        self.assertEqual(
            correct_unknown_token("mystrey", {"mystery"}),
            "mystery",
        )
        self.assertEqual(
            correct_unknown_token(
                "cat",
                {"bat", "hat"},
                document_frequency={"bat": 2, "hat": 9},
            ),
            "hat",
        )
        self.assertIsNone(
            correct_unknown_token(
                "cat",
                {"bat", "hat"},
                document_frequency={"bat": 2, "hat": 2},
            )
        )

    def test_substring_collision_is_not_treated_as_a_typo(self):
        self.assertIsNone(correct_unknown_token("warm", {"war"}))


class AmbiguousWordTests(unittest.TestCase):
    def test_entropy_detection_uses_synthetic_catalog_genres(self):
        genres = ["drama", "comedy", "horror"]
        movies = [
            {"genres": [genre], "synopsis": "light"}
            for genre in genres for _ in range(2)
        ] + [
            {"genres": [genre], "synopsis": "isolated"}
            for genre in genres
        ]
        ambiguous = _high_entropy_terms(movies)
        self.assertIn("light", ambiguous)
        self.assertNotIn("isolated", ambiguous)

    def test_ambiguous_words_need_a_second_signal_to_filter(self):
        light = extract_preferences("light")
        self.assertEqual(light["moods"], [])
        self.assertEqual(light["genres"], [])
        self.assertEqual(
            light["ambiguous_interpretations"],
            [{
                "surface": "light",
                "category": "mood",
                "tag": "relaxing",
                "negated": False,
            }],
        )
        understanding = local_understanding("light")
        self.assertEqual(understanding["mood_tone"], [])
        self.assertIn("relaxing", understanding["keywords"])
        self.assertNotIn("relaxing", understanding["required_terms"])
        self.assertIn("light", _catalog_ambiguous_terms())

        clarified = extract_preferences("light comedy")
        self.assertIn("relaxing", clarified["moods"])
        self.assertIn("comedy", clarified["genres"])
        self.assertEqual(clarified["ambiguous_interpretations"], [])
        self.assertEqual(local_understanding("comedy")["genres"], [])

    def test_synonym_category_collisions_are_detected_from_tables(self):
        self.assertIn("comedy", _catalog_ambiguous_terms())


if __name__ == "__main__":
    unittest.main()
