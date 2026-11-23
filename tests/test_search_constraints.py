"""Regression tests for explicit runtime limits and required content terms."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import movie_matches_runtime_limits
from data.movies import _restore_curated_search_metadata
from nlp.extract import extract_preferences
from nlp.query import build_structured_query
from nlp.scoring import is_excluded
from nlp.smart_search import (
    _BM25_CACHE,
    _basic_assessment,
    _fuzzy_term_map,
    _get_bm25_index,
    _query_terms,
    local_understanding,
    retrieve_candidates,
)
from nlp.tfidf import load_or_build_tfidf_model


def movie(movie_id, title, synopsis, runtime=90):
    return {
        "id": movie_id, "title": title, "synopsis": synopsis,
        "genres": ["drama"], "release_year": 2000, "runtime": runtime,
        "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
    }


class CandidateIndexTests(unittest.TestCase):
    def test_bm25_cache_rejects_reused_list_identity(self):
        first = [movie("first", "First Beacon", "A quiet drama.")]
        second = [movie("second", "Second Signal", "A suspenseful mystery.")]
        first_index = _get_bm25_index(first)

        with patch.dict(_BM25_CACHE, {id(second): (first, first_index)}):
            second_index = _get_bm25_index(second)

        self.assertIn("signal", second_index["vocabulary"])
        self.assertNotIn("beacon", second_index["vocabulary"])


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

    def test_imported_template_does_not_hide_curated_search_metadata(self):
        query = "quiet mystery under two hours"
        imported = {
            "id": "synthetic-import",
            "title": "Synthetic Beacon",
            "synopsis": "Synthetic Beacon (2004), a mystery movie.",
            "genres": ["mystery"],
            "themes": [],
            "mood_tags": [],
            "keywords": ["Synthetic Beacon", "mystery"],
            "content_descriptors": [],
            "runtime": 108,
            "release_year": 2004,
        }
        curated = {
            "id": 1,
            "title": "Synthetic Beacon",
            "synopsis": "A quiet mystery follows a courier decoding an unusual signal.",
            "genres": ["mystery"],
            "themes": ["signals"],
            "mood_tags": ["suspenseful"],
            "keywords": ["quiet", "courier", "signal"],
            "content_descriptors": [],
            "runtime": 108,
            "release_year": 2004,
        }
        record = _restore_curated_search_metadata([imported], [curated])[0]
        understanding = local_understanding(query)

        self.assertEqual(understanding["filters"]["runtime"]["max"], 119)
        self.assertNotIn("two", understanding["required_terms"])
        self.assertEqual(record["id"], "synthetic-import")
        self.assertIn("quiet mystery", record["synopsis"])
        self.assertEqual(
            [item["movie"]["id"] for item in retrieve_candidates(query, understanding, [record])],
            ["synthetic-import"],
        )

    def test_tfidf_cache_rebuilds_when_searchable_metadata_changes(self):
        record = movie("synthetic-import", "Synthetic Beacon", "A mystery movie.")
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "tfidf.pkl"
            initial_model = load_or_build_tfidf_model([record], cache_path)
            self.assertNotIn("courier", initial_model["vectors"][record["id"]])

            record["synopsis"] = "A quiet mystery follows a courier decoding a signal."
            rebuilt_model = load_or_build_tfidf_model([record], cache_path)

        self.assertIn("courier", rebuilt_model["vectors"][record["id"]])

    def test_taglish_structured_query_retrieves_genre_candidates(self):
        query = "Gusto ko ng suspenseful na mystery movie"
        candidate = movie("synthetic-mystery", "Synthetic Mystery", "A detective follows a series of clues.")
        candidate["genres"] = ["mystery"]
        understanding = local_understanding(query)

        results = retrieve_candidates(query, understanding, [candidate])

        self.assertEqual([item["movie"]["id"] for item in results], ["synthetic-mystery"])

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
    def test_catalog_genre_aliases_resolve_without_music_musical_collision(self):
        for query, expected in (
            ("biography movies", "biography"),
            ("history movies", "history"),
            ("sports movies", "sport"),
            ("war movies", "war"),
            ("western movies", "western"),
            ("music movies", "music"),
        ):
            with self.subTest(query=query):
                self.assertEqual(extract_preferences(query)["genres"], [expected])

    def test_four_word_negated_mood_phrase_is_extracted(self):
        preferences = extract_preferences("movies without edge of your seat")
        self.assertEqual(preferences["excluded_moods"], ["suspenseful"])

    def test_removing_keyword_exclusion_clears_all_verification_state(self):
        preferences = {"excluded_keywords": ["harbor"]}
        understanding = {
            "filters": {"excluded_keywords": ["harbor"]},
            "hard_constraints": ["exclude keyword harbor"],
            "negative_requirements": ["harbor"],
        }

        from app import drop_constraints
        drop_constraints(preferences, understanding, ["exclude_keyword:harbor"])

        self.assertEqual(preferences["excluded_keywords"], [])
        self.assertEqual(understanding["filters"]["excluded_keywords"], [])
        self.assertEqual(understanding["hard_constraints"], [])
        self.assertEqual(understanding["negative_requirements"], [])

    def test_negated_free_text_is_an_exclusion_not_a_positive_keyword(self):
        preferences = extract_preferences("movies without harbor")
        self.assertEqual(preferences["excluded_keywords"], ["harbor"])
        self.assertNotIn("harbor", preferences["free_text_keywords"])

        stopword_exclusion = extract_preferences("movies without about")
        self.assertEqual(stopword_exclusion["excluded_keywords"], ["about"])

    def test_negated_year_is_not_reinterpreted_as_a_year_filter(self):
        preferences = extract_preferences("movies without 2012")
        self.assertIsNone(preferences["release_year"])
        self.assertEqual(preferences["excluded_keywords"], ["2012"])

        runtime_preferences = extract_preferences("movies no more than 90 minutes")
        self.assertEqual(runtime_preferences["runtime"]["max"], 90)
        self.assertEqual(runtime_preferences["excluded_keywords"], [])

    def test_negated_country_alias_and_fuzzy_synonym_stay_exclusions(self):
        country_term = build_structured_query("movies without america")
        self.assertEqual(country_term["country"], "")
        self.assertEqual(country_term["preferences"]["excluded_keywords"], ["america"])

        synonym_term = extract_preferences("movies without adventures")
        self.assertEqual(synonym_term["excluded_genres"], ["adventure"])
        self.assertEqual(synonym_term["excluded_keywords"], ["adventures"])

    def test_country_names_inside_plot_phrases_remain_search_text(self):
        for query in (
            "movies about boyfriend singapore",
            "movies about adoption peruvian",
            "movies about antarctica appearance",
        ):
            with self.subTest(query=query):
                self.assertEqual(build_structured_query(query)["country"], "")

    def test_overlapping_ranking_and_taglish_synonyms_preserve_intent(self):
        feel_good = build_structured_query("movies with feel good")
        self.assertEqual(feel_good["ranking_intent"], "")
        self.assertEqual(feel_good["preferences"]["moods"], ["happy"])

        taglish = build_structured_query("nakakatawa na movies")
        self.assertIn("comedy", taglish["preferences"]["genres"])

    def test_description_tokens_split_hyphenated_catalog_terms(self):
        candidate = movie(14, "Synthetic", "Two co-dependent friends face impending change.")
        understanding = local_understanding("movies about dependent impending")
        results = retrieve_candidates(
            "movies about dependent impending", understanding, [candidate],
        )
        self.assertEqual([item["movie"]["id"] for item in results], [14])

    def test_negative_requirements_do_not_bias_lexical_retrieval(self):
        query = "comedy movie from 2006 under 137 minutes without action"
        understanding = local_understanding(query)
        terms = _query_terms(query, understanding)
        self.assertNotIn("action", terms)
        self.assertNotIn("minut", terms)

    def test_description_word_is_not_misread_as_a_person_name(self):
        parsed = build_structured_query("movies about actress weigh")
        self.assertEqual(parsed["people"], [])

    def test_excluded_synonyms_are_checked_across_title_and_synopsis(self):
        candidate = movie(11, "International Man of Mystery", "A comic spy adventure.")
        self.assertTrue(is_excluded(candidate, {
            "excluded_genres": ["mystery"],
            "excluded_moods": [],
            "excluded_themes": [],
            "excluded_content_descriptors": [],
            "excluded_keywords": [],
        }))

    def test_phrase_exclusions_do_not_join_separate_metadata_fields(self):
        candidate = movie(12, "Synthetic", "A family story.")
        candidate["genres"] = ["drama"]
        self.assertFalse(is_excluded(candidate, {
            "excluded_genres": [],
            "excluded_moods": [],
            "excluded_themes": ["family secrets"],
            "excluded_content_descriptors": [],
            "excluded_keywords": [],
        }))

    def test_exclusion_matching_normalizes_superscripts_and_plural_forms(self):
        self.assertTrue(is_excluded(
            movie(13, "Alien\u00b3", "An adventure."),
            {
                "excluded_genres": [],
                "excluded_moods": [],
                "excluded_themes": [],
                "excluded_content_descriptors": [],
                "excluded_keywords": ["alien3", "adventures"],
            },
        ))

    def test_keyword_exclusions_scan_country_cast_certification_and_awards(self):
        cases = (
            ("production_countries", ["America"], "america"),
            ("cast", [{"name": "Alice Example", "character": "Detective"}], "alice"),
            ("certification", "PG-13", "pg"),
            ("notable_awards", ["Academy Award"], "academy"),
        )
        for field, value, term in cases:
            candidate = movie(15, "Synthetic", "A quiet story.")
            candidate[field] = value
            with self.subTest(field=field):
                self.assertTrue(is_excluded(candidate, {
                    "excluded_genres": [],
                    "excluded_moods": [],
                    "excluded_themes": [],
                    "excluded_content_descriptors": [],
                    "excluded_keywords": [term],
                }))

    def test_fuzzy_matching_does_not_change_connectors_or_numbers(self):
        mapping = _fuzzy_term_map(
            ["under", "90", "the", "mystrey"],
            {"hunter", "mystery"},
        )
        self.assertEqual(mapping, {
            "under": "under", "90": "90", "the": "the", "mystrey": "mystery",
        })

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