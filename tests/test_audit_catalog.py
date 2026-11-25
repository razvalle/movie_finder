"""Unit tests for the catalog audit using synthetic records."""

import csv
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.audit_catalog import (
    audit,
    clean_catalog,
    has_real_plot,
    malformed_reasons,
    write_catalog_audit_log,
)
from data import import_imdb


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
        self.assertEqual(len(report["duplicates"]["duplicate_title_year_runtime"]), 1)

    def test_malformed_records(self):
        self.assertEqual(malformed_reasons(record()), [])
        problems = malformed_reasons(record(title="", release_year=1500, runtime=9000, genres=[], average_rating=11))
        self.assertEqual(len(problems), 5)

    def test_cleaning_filters_types_and_adult_flags_and_logs_each_record(self):
        source = [
            record(title="Episode", title_type="tvEpisode"),
            record(id=2, imdb_id="tt2", title="Series", title_type="tvSeries"),
            record(id=3, imdb_id="tt3", title="Short", title_type="short"),
            record(id=4, imdb_id="tt4", title="Adult", is_adult=True),
            record(id=5, imdb_id="tt5", title="Keep", title_type="movie"),
        ]
        cleaned, events = clean_catalog(source)
        self.assertEqual([movie["title"] for movie in cleaned], ["Keep"])
        self.assertEqual([event["action"] for event in events], ["remove"] * 4)
        self.assertEqual(events[0]["record"]["title"], "Episode")
        self.assertEqual(events[3]["record"]["title"], "Adult")

    def test_cleaning_normalizes_text_genres_and_impossible_values(self):
        current_year = 2026
        cleaned, events = clean_catalog([
            record(
                title="  CafÃ©   Test ",
                synopsis="   ",
                director="",
                original_title="\\N",
                release_year=current_year + 1,
                runtime=0,
                genres=[" Science Fiction ", "sci-fi", "  ", "---"],
            ),
            record(id=2, imdb_id="tt2", title="Runtime edge", runtime=601, release_year=1879),
            record(id=3, imdb_id="tt3", title="Alien³"),
        ], current_year=current_year)
        movie, edge_case, compatibility_title = cleaned
        self.assertEqual(movie["title"], "Café Test")
        self.assertIsNone(movie["synopsis"])
        self.assertIsNone(movie["director"])
        self.assertIsNone(movie["original_title"])
        self.assertIsNone(movie["runtime"])
        self.assertIsNone(movie["release_year"])
        self.assertEqual(len(movie["genres"]), 1)
        self.assertIsNone(edge_case["runtime"])
        self.assertIsNone(edge_case["release_year"])
        self.assertEqual(compatibility_title["title"], "Alien³")
        self.assertTrue(events)

    def test_fallback_dedup_merges_missing_fields(self):
        first = {
            "title": "North-Star", "release_year": 2000, "runtime": 100,
            "genres": ["drama"], "keywords": [],
        }
        duplicate = {
            "title": "North Star", "release_year": 2000, "runtime": 100,
            "genres": ["adventure"], "keywords": ["journey"],
        }
        cleaned, events = clean_catalog([first, duplicate])
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["keywords"], ["journey"])
        self.assertEqual(cleaned[0]["genres"], ["drama", "adventure"])
        self.assertEqual([event["action"] for event in events], ["merge"])

    def test_stable_id_dedup_merges_missing_scalar_fields(self):
        first = record(synopsis=None, director=None)
        duplicate = record(synopsis="A real synopsis.", director="A Director")
        cleaned, events = clean_catalog([first, duplicate])
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["synopsis"], "A real synopsis.")
        self.assertEqual(cleaned[0]["director"], "A Director")
        self.assertEqual([event["action"] for event in events], ["merge"])

    def test_audit_log_persists_removed_and_merged_records(self):
        cleaned, events = clean_catalog([
            record(title="Series", title_type="tvSeries"),
            {"title": "Same", "release_year": 2000, "runtime": 100, "genres": ["drama"]},
            {"title": "Same!", "release_year": 2000, "runtime": 100, "genres": ["drama"]},
        ])
        self.assertEqual(len(cleaned), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.jsonl"
            write_catalog_audit_log(events, path)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["action"] for row in rows], ["remove", "merge"])
        self.assertEqual(rows[0]["record"]["title"], "Series")

    def test_imdb_import_keeps_unknown_fields_and_logs_exclusions(self):
        fields = [
            "tconst", "titleType", "primaryTitle", "originalTitle", "isAdult",
            "startYear", "endYear", "runtimeMinutes", "genres",
        ]
        rows = [
            ["ttvalid", "movie", "Valid", "Valid", "0", "2020", "2020", "105", "Drama,Comedy"],
            ["ttseries", "tvSeries", "Series", "Series", "0", "2020", "2020", "\\N", "Drama"],
            ["ttadult", "movie", "Adult", "Adult", "1", "2020", "2020", "100", "Drama"],
            ["ttfuture", "movie", "Future", "Future", "0", "2027", "\\N", "0", "\\N"],
            ["ttunknown", "movie", "Unknown", "\\N", "0", "\\N", "\\N", "\\N", "\\N"],
            ["ttold", "movie", "Old", "Old", "0", "1889", "1889", "100", "Drama"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            basics = root / "title.basics.tsv.gz"
            with gzip.open(basics, "wt", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t")
                writer.writerow(fields)
                writer.writerows(rows)
            output = root / "imdb_movies.json"
            with patch.object(import_imdb, "BASICS_PATH", basics), \
                    patch.object(import_imdb, "RATINGS_PATH", root / "missing-ratings.gz"), \
                    patch.object(import_imdb, "AKAS_PATH", root / "missing-akas.gz"), \
                    patch.object(import_imdb, "DATA_DIR", root), \
                    patch.object(import_imdb, "OUTPUT_PATH", output), \
                    patch.object(import_imdb, "END_YEAR", 2026):
                movies = import_imdb.build_catalog()

            by_title = {movie["title"]: movie for movie in movies}
            self.assertEqual(set(by_title), {"Valid", "Future", "Unknown"})
            self.assertEqual(by_title["Valid"]["runtime"], 105)
            self.assertIsNone(by_title["Future"]["runtime"])
            self.assertIsNone(by_title["Future"]["release_year"])
            self.assertEqual(by_title["Future"]["genres"], [])
            self.assertIsNone(by_title["Unknown"]["release_year"])
            self.assertIsNone(by_title["Unknown"]["runtime"])
            self.assertIsNone(by_title["Unknown"]["original_title"])
            events = [
                json.loads(line)
                for line in (root / "imdb_import_audit.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            reasons = {event["reason"] for event in events}
            self.assertIn("non-movie title_type", reasons)
            self.assertIn("adult-flagged record", reasons)
            self.assertIn("outside configured year floor", reasons)
            self.assertIn("future release year set to unknown", reasons)


if __name__ == "__main__":
    unittest.main()
