"""Regression tests for the rule-based structured natural-language query pipeline."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.query import build_structured_query


class StructuredQueryTests(unittest.TestCase):
    def assert_query(self, text, country="", ranking="", genres=(), excluded=(), year=None, themes=()):
        parsed = build_structured_query(text)
        preferences = parsed["preferences"]
        self.assertEqual(parsed["country"], country, text)
        self.assertEqual(parsed["ranking_intent"], ranking, text)
        self.assertTrue(set(genres).issubset(preferences["genres"]), text)
        self.assertTrue(set(excluded).issubset(preferences["excluded_genres"]), text)
        self.assertTrue(set(themes).issubset(preferences["themes"]), text)
        if year is not None:
            self.assertEqual(preferences["release_year"], year, text)
        return parsed

    def test_requested_country_and_ranking_queries(self):
        self.assert_query("best ph movies", country="PH", ranking="best")
        self.assert_query("best filipino movies", country="PH", ranking="best")
        self.assert_query("best pinoy movies", country="PH", ranking="best")
        self.assert_query("highest rated movies from japan", country="JP", ranking="best")

    def test_requested_genre_aliases(self):
        self.assert_query("best pinoy horror movies", country="PH", ranking="best", genres=("horror",))
        self.assert_query("scary movies from pinas", country="PH", genres=("horror",))
        self.assert_query("best sci-fi movies", ranking="best", genres=("sci-fi",))
        self.assert_query("romcom movies", genres=("romance", "comedy"))

    def test_requested_dates_and_negation(self):
        self.assert_query("filipino horror movies from 2020", country="PH", genres=("horror",), year={"min": 2020, "max": 2020})
        self.assert_query("funny korean movies from 2020", country="KR", year={"min": 2020, "max": 2020})
        self.assert_query("movies from 2015 to 2020", year={"min": 2015, "max": 2020})
        self.assert_query("movies after 2020", year={"min": 2021, "max": None})
        self.assert_query("movies before 2010", year={"min": None, "max": 2009})
        self.assert_query("movies without romance", excluded=("romance",))

    def test_explicit_filters_do_not_pollute_description(self):
        parsed = self.assert_query(
            "best Filipino horror movies about a haunted house",
            country="PH",
            ranking="best",
            genres=("horror",),
            themes=("haunting",),
        )
        self.assertNotIn("best", parsed["semantic_description"])
        self.assertNotIn("filipino", parsed["semantic_description"])
        self.assertNotIn("horror", parsed["semantic_description"])

    def test_ranking_people_and_language_entities(self):
        ranking = build_structured_query("best movies")
        self.assertEqual(ranking["ranking_intent"], "best")
        self.assertEqual(ranking["semantic_description"], [])

        people = build_structured_query("movies directed by christopher nolan")
        self.assertIn("christopher nolan", people["people"])

        language = build_structured_query("japanese language movies")
        self.assertIn("japanese", language["languages"])


if __name__ == "__main__":
    unittest.main()
