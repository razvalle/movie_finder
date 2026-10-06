"""Unit tests for the catalog audit using synthetic records."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.audit_catalog import audit, has_real_plot, malformed_reasons


def record(**overrides):
    base = {"id": 1, "imdb_id": "tt1", "title": "Alpha", "release_year": 2000, "runtime": 100,
            "genres": ["drama"], "synopsis": "A fisherman loses his boat.", "average_rating": 7.0}
    base.update(overrides)
    return base


class AuditTests(unittest.TestCase):
    def test_template_synopsis_is_not_a_real_plot(self):
        self.assertFalse(has_real_plot(record(synopsis="IMDb rating 8.6/10 from 2,060,884 votes; released in 1995. Genres: crime.")))
        self.assertFalse(has_real_plot(record(synopsis="Alpha (2000), a drama movie.")))
        self.assertTrue(has_real_plot(record()))

    def test_coverage_counts_present_fields(self):
        report = audit([
            record(original_title="Alpha (Original)", keywords=["Alpha"], origin_regions=["US"], is_adult=False),
            record(id=2, imdb_id="tt2", title="Beta", runtime=0, poster_path="/b.jpg"),
        ])
        self.assertEqual(report["coverage"]["runtime"], 1)
        self.assertEqual(report["coverage"]["poster"], 1)
        self.assertEqual(report["coverage"]["original_title"], 1)
        self.assertEqual(report["coverage"]["IMDb keywords"], 1)
        self.assertEqual(report["coverage"]["IMDb release regions"], 1)

    def test_metadata_limitations_report_key_state_and_synthetic_scope(self):
        with patch.dict("os.environ", {}, clear=True):
            report = audit([record()])
        self.assertFalse(report["metadata_status"]["tmdb_key_configured"])
        self.assertIn("fabricated", report["metadata_status"]["fabrication"])
        self.assertIn("synthetic evidence", report["metadata_status"]["synthetic_benchmark_priority"])

    def test_duplicates_detected(self):
        report = audit([record(), record(id=2), record(id=3, imdb_id="tt3", title="alpha!")])
        self.assertEqual(report["duplicates"]["duplicate_imdb_ids"], ["tt1"])
        self.assertEqual(len(report["duplicates"]["duplicate_title_year"]), 1)

    def test_malformed_records(self):
        self.assertEqual(malformed_reasons(record()), [])
        problems = malformed_reasons(record(title="", release_year=1500, runtime=9000, genres=[], average_rating=11))
        self.assertEqual(len(problems), 5)


if __name__ == "__main__":
    unittest.main()
