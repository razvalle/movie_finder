"""Tests for removable criteria chips and the offline feedback report."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, build_preference_tags, describe_tag, drop_constraints
from feedback_report import summarize


def preferences():
    return {
        "genres": ["comedy"], "moods": [], "themes": [], "excluded_genres": ["romance"],
        "excluded_moods": [], "excluded_themes": [], "free_text_keywords": [],
        "runtime": {"min": None, "max": 100, "target": None}, "release_year": {"min": 1990, "max": 1999},
    }


def understanding():
    return {
        "genres": ["comedy"], "mood_tone": [], "exclusions": ["romance"],
        "year_range": {"from": 1990, "to": 1999},
        "filters": {"genres": ["comedy"], "year_range": {"from": 1990, "to": 1999}},
        "hard_constraints": ["release year", "exclude romance"],
    }


class ChipTests(unittest.TestCase):
    def test_every_tag_has_a_drop_key(self):
        keys = [t["key"] for t in build_preference_tags(preferences(), 0)]
        self.assertEqual(keys, ["genre:comedy", "exclude_genre:romance", "runtime", "year"])

    def test_drop_removes_only_the_named_constraint(self):
        prefs, und = preferences(), understanding()
        drop_constraints(prefs, und, ["exclude_genre:romance", "year"])
        self.assertEqual(prefs["excluded_genres"], [])
        self.assertEqual(prefs["genres"], ["comedy"])
        self.assertIsNone(prefs["release_year"])
        self.assertEqual(und["hard_constraints"], [])
        self.assertEqual(und["exclusions"], [])

    def test_route_renders_chip_with_remove_link(self):
        with patch("app.allow_search", return_value=True):
            html = app.test_client().get("/?q=comedy+movies+from+the+90s+without+romance").get_data(as_text=True)
        self.assertIn("pref-tag__remove", html)
        self.assertIn("drop=genre:comedy", html)

    def test_dropping_year_removes_year_chip(self):
        with patch("app.allow_search", return_value=True):
            html = app.test_client().get("/?q=comedy+movies+from+the+90s&drop=year").get_data(as_text=True)
        self.assertNotIn('pref-tag__label">Year<', html)


class FeedbackReportTests(unittest.TestCase):
    def test_summary_orders_worst_first_and_is_read_only(self):
        votes = [("a", 1, "Good", 1)] * 3 + [("b", 2, "Bad", 0)] * 3 + [("b", 2, "Bad", 1)]
        summary = summarize(votes, min_votes=3)
        self.assertEqual(summary["movies"][0][0], "Bad")
        self.assertEqual(summary["queries"][0][0], "b")
        self.assertAlmostEqual(summary["relevant_rate"], 4 / 7)


if __name__ == "__main__":
    unittest.main()
