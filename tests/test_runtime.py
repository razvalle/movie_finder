"""Regression tests for numeric runtime and decade parsing."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.release_year import extract_release_year_constraint
from nlp.runtime import extract_runtime_constraint


class RuntimeParsingTests(unittest.TestCase):
    def test_strict_runtime_bounds_exclude_the_endpoint(self):
        self.assertEqual(extract_runtime_constraint("under 90 minutes")["max"], 89)
        self.assertEqual(extract_runtime_constraint("less than 90 minutes")["max"], 89)
        self.assertEqual(extract_runtime_constraint("over 90 minutes")["min"], 91)
        self.assertEqual(extract_runtime_constraint("at least 90 minutes")["min"], 90)
        self.assertEqual(extract_runtime_constraint("no more than 90 minutes")["max"], 90)

    def test_runtime_range_and_compact_hour_minute_forms(self):
        self.assertEqual(
            extract_runtime_constraint("between 90 and 120 minutes"),
            {"min": 90, "max": 120, "target": None, "tolerance": 15},
        )
        self.assertEqual(extract_runtime_constraint("under 1h30")["max"], 89)
        self.assertEqual(extract_runtime_constraint("around 1h30")["target"], 90)
        self.assertEqual(extract_runtime_constraint("two and a half hours")["target"], 150)

    def test_spelled_out_decade_matches_digit_forms(self):
        expected = {"min": 1990, "max": 1999}
        self.assertEqual(extract_release_year_constraint("movies from the 90s"), expected)
        self.assertEqual(extract_release_year_constraint("movies from the 1990s"), expected)
        self.assertEqual(extract_release_year_constraint("movies from the nineties"), expected)


if __name__ == "__main__":
    unittest.main()
