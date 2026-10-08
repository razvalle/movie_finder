"""Tests for the plain-language 'I understood' sentence and its remove links."""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, describe_tag
from nlp.conversation import build_alternative_queries


def tag(label, value):
    return {"label": label, "value": value}


class DescribeTagTests(unittest.TestCase):
    def test_phrases(self):
        self.assertEqual(describe_tag(tag("Genre", "Comedy")), "comedy")
        self.assertEqual(describe_tag(tag("Exclude", "Romance")), "excluding romance")
        self.assertEqual(describe_tag(tag("Runtime", "< 100 min")), "under 100 min")
        self.assertEqual(describe_tag(tag("Runtime", "> 90 min")), "over 90 min")
        self.assertEqual(describe_tag(tag("Runtime", "~ 100 min")), "about 100 min")
        self.assertEqual(describe_tag(tag("Year", "1990-1999")), "1990s")
        self.assertEqual(describe_tag(tag("Year", "2015-2020")), "from 2015-2020")
        self.assertEqual(describe_tag(tag("Year", "after 2010")), "after 2010")


class AlternativeQueryTests(unittest.TestCase):
    def test_alternatives_are_built_from_current_search_preferences(self):
        alternatives = build_alternative_queries(
            {"alternative_queries": []},
            {"genres": ["mystery"], "moods": ["suspenseful"], "themes": ["investigation"]},
        )

        self.assertEqual(alternatives[0], "suspenseful investigation mystery movies")
        self.assertIn("mystery movies", alternatives)
        self.assertNotIn("popular comedy movies", alternatives)

    def test_no_unrelated_defaults_for_unrecognized_search(self):
        self.assertEqual(build_alternative_queries({"alternative_queries": []}), [])


class InterpretationRouteTests(unittest.TestCase):
    def get(self, query_string):
        with patch("app.allow_search", return_value=True):
            return app.test_client().get("/", query_string=query_string).get_data(as_text=True)

    def sentence(self, html):
        match = re.search(r'<p class="interpretation">(.*?)</p>', html, re.S)
        return re.sub(r"<[^>]+>|\s+", " ", match.group(1)).replace("×", "").strip() if match else ""

    def test_sentence_lists_parsed_parts(self):
        text = self.sentence(self.get({"q": "comedy movies from the 90s without romance"}))
        self.assertIn("I understood:", text)
        for phrase in ("comedy", "1990s", "excluding romance"):
            self.assertIn(phrase, text)

    def test_removing_a_part_reruns_search_without_only_that_part(self):
        html = self.get({"q": "comedy movies from the 90s without romance"})
        href = re.search(r'href="([^"]*drop=exclude_genre:romance[^"]*)"', html).group(1).replace("&amp;", "&")
        self.assertTrue(href.startswith("/?q="))
        after = self.sentence(self.get(href.split("?", 1)[1]))
        self.assertNotIn("excluding romance", after)
        self.assertIn("comedy", after)
        self.assertIn("1990s", after)

    def test_no_sentence_without_a_query(self):
        self.assertEqual(self.sentence(self.get({})), "")


if __name__ == "__main__":
    unittest.main()
