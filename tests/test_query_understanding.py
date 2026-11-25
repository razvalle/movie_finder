"""Focused regressions for title, negation, runtime, and query intent."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    app as flask_app,
    build_preference_tags,
    find_filter_conflicts,
    find_title_matches,
)
from data.movies import MOVIES
from nlp.extract import extract_preferences
from nlp.query import build_structured_query
from nlp.runtime import (
    extract_runtime_constraint,
    extract_vague_runtime_constraint,
)


class QueryUnderstandingTests(unittest.TestCase):
    def test_short_titles_are_only_embedded_when_explicit(self):
        self.assertIn("Up", [movie["title"] for movie in find_title_matches("Up", MOVIES)])
        self.assertIn(
            "Up",
            [movie["title"] for movie in find_title_matches("movie called Up", MOVIES)],
        )
        self.assertIn(
            "Up",
            [movie["title"] for movie in find_title_matches('watch "Up" again', MOVIES)],
        )
        self.assertEqual(find_title_matches("I want to go up the stairs", MOVIES), [])

    def test_movie_suffix_in_a_title_is_not_discarded_as_filler(self):
        titles = [
            movie["title"] for movie in find_title_matches("The Lego Movie", MOVIES)
        ]
        self.assertIn("The Lego Movie", titles)

    def test_title_wins_but_embedded_genre_reading_is_removable(self):
        client = flask_app.test_client()
        exact = client.get("/", query_string={"q": "The Dark Knight"})
        self.assertIn(b"The Dark Knight", exact.data)
        self.assertNotIn(b"Did you mean", exact.data)

        embedded = client.get(
            "/", query_string={"q": "The Dark Knight adventure"},
        )
        self.assertIn(b"Dark as a mood?", embedded.data)
        self.assertIn(b"Adventure", embedded.data)
        self.assertIn(b"Remove Dark as a mood?", embedded.data)

    def test_bare_numeric_title_keeps_removable_year_alternative(self):
        response = flask_app.test_client().get("/", query_string={"q": "2012"})
        self.assertIn(b"2012 as a year?", response.data)
        self.assertIn(b"Remove 2012 as a year?", response.data)

    def test_hedged_negation_is_soft_and_postposed_negation_is_scoped(self):
        hedged = extract_preferences("not too violent")
        self.assertEqual(hedged["soft_exclusions"], ["content_descriptor:violence"])
        self.assertEqual(hedged["excluded_content_descriptors"], [])
        for query in (
            "horror no thanks",
            "no horror please",
            "horror is not what i want",
            "I'd rather not horror",
            "anything but horror",
            "non-horror",
        ):
            with self.subTest(query=query):
                self.assertIn("horror", extract_preferences(query)["excluded_genres"])
        free_of = extract_preferences("gore-free horror")
        self.assertIn("violence", free_of["excluded_content_descriptors"])
        self.assertNotIn("horror", free_of["excluded_genres"])
        self.assertTrue(extract_preferences("avoid")["unclear_negation"])
        self.assertFalse(extract_preferences("no thanks")["unclear_negation"])

    def test_vague_runtime_uses_catalog_percentiles_and_displays_boundary(self):
        synthetic = [80, 90, 100, 110]
        self.assertEqual(
            extract_vague_runtime_constraint("short", synthetic)["max"], 90,
        )
        self.assertEqual(
            extract_vague_runtime_constraint("long", synthetic)["min"], 100,
        )
        preferences = extract_preferences("quick")
        runtime_tag = next(
            tag for tag in build_preference_tags(preferences, 0)
            if tag["label"] == "Runtime"
        )
        self.assertEqual(
            runtime_tag["value"],
            f"< {preferences['runtime']['max']} min",
        )

    def test_runtime_and_year_conflicts_keep_both_sides(self):
        runtime = extract_runtime_constraint("at least 120 minutes and under 90 minutes")
        self.assertEqual(runtime["min"], 120)
        self.assertEqual(runtime["max"], 89)
        self.assertIsNone(runtime["target"])
        year = build_structured_query("movies after 2020 and before 2000")["preferences"]["release_year"]
        self.assertEqual(year, {"min": 2021, "max": 1999})
        conflicts = find_filter_conflicts({
            "genres": [], "excluded_genres": [],
            "moods": [], "excluded_moods": [],
            "themes": [], "excluded_themes": [],
            "excluded_content_descriptors": [],
            "runtime": runtime,
            "release_year": year,
        })
        self.assertTrue(any("runtime" in conflict for conflict in conflicts))
        self.assertTrue(any("year" in conflict for conflict in conflicts))

    def test_conflicts_match_synonyms_and_keep_chip_removal_visible(self):
        preferences = {
            "genres": ["comedy"], "excluded_genres": ["comedies"],
            "moods": [], "excluded_moods": [],
            "themes": [], "excluded_themes": [],
            "excluded_content_descriptors": [],
            "runtime": {"min": None, "max": None},
            "release_year": None,
        }
        conflicts = find_filter_conflicts(preferences)
        self.assertTrue(any("exclusions win" in conflict for conflict in conflicts))
        response = flask_app.test_client().get(
            "/", query_string={"q": "no comedy", "genre": "comedy"},
        )
        self.assertIn(b"exclusions win", response.data)
        self.assertIn(b"Remove comedy", response.data)

    def test_audience_mappings_and_superlatives_are_structured(self):
        audience = build_structured_query("family night")["preferences"]
        self.assertIn("family", audience["audience_intents"])
        self.assertEqual(
            audience["audience_certification_limits"]["family"],
            "PG",
        )
        self.assertEqual(build_structured_query("highest rated")["ranking_intent"], "best")
        self.assertEqual(build_structured_query("lowest rated")["ranking_intent"], "worst")


if __name__ == "__main__":
    unittest.main()
