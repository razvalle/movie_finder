import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import enrich_cast


class CastEnrichmentTests(unittest.TestCase):
    def test_missing_api_key_is_inert(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(enrich_cast, "ensure_dirs") as ensure_dirs, patch.object(
            enrich_cast, "load_catalog", side_effect=AssertionError("catalog must not be read without the key")
        ):
            self.assertEqual(enrich_cast.enrich_catalog(), 0)
        ensure_dirs.assert_not_called()

    def test_deployment_target_is_curated_plus_popular_catalog(self):
        with patch.dict("os.environ", {"TMDB_CATALOG_PATH": "deployment"}):
            catalog = enrich_cast.load_catalog()
        self.assertEqual(len(catalog), 1065)
        self.assertEqual(catalog[0]["id"], 1)
        self.assertEqual(catalog[-1]["id"], 1065)

    def test_title_year_runtime_fallback_rejects_runtime_mismatch(self):
        movie = {"title": "Exact Title", "release_year": 2001, "runtime": 100}
        search_payload = {
            "results": [{
                "id": 12,
                "title": "Exact Title",
                "original_title": "Exact Title",
                "release_date": "2001-04-05",
            }]
        }
        with patch.object(enrich_cast, "request_json", return_value=search_payload), patch.object(
            enrich_cast, "fetch_movie_details", return_value=({"runtime": 135}, {})
        ):
            result, reason = enrich_cast.match_tmdb_movie(movie, "test-key")
        self.assertIsNone(result)
        self.assertEqual(reason, "unmatched")

    def test_enrichment_resume_preserves_records_and_writes_coverage_report(self):
        movie = {
            "id": 1,
            "imdb_id": "tt0000001",
            "title": "The Unit",
            "release_year": 2001,
            "runtime": 100,
            "genres": ["drama"],
            "synopsis": "A team enters a town.",
            "keywords": ["The Unit"],
        }
        details = {
            "overview": "A unit crosses a border under cover of night.",
            "tagline": "",
            "runtime": 106,
            "original_language": "en",
            "poster_path": "/sample-poster.jpg",
            "production_countries": [{"iso_3166_1": "GB"}, {"iso_3166_1": "US"}],
            "keywords": {"keywords": [{"name": "military"}]},
            "release_dates": {"results": []},
        }
        credits = {
            "cast": [
                {"name": f"Actor {index}", "character": "Soldier", "order": index, "gender": 2}
                for index in range(5)
            ]
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = {
                "CACHE_DIR": root / "cache",
                "TARGET_PATH": root / "movie_cast_enrichment.json",
                "PROGRESS_PATH": root / "progress.json",
                "REVIEW_CSV": root / "review.csv",
                "REPORT_PATH": root / "report.json",
            }
            with patch.dict("os.environ", {"TMDB_API_KEY": "test-key"}), \
                    patch.object(enrich_cast, "load_catalog", return_value=[movie]), \
                    patch.object(enrich_cast, "match_tmdb_movie", return_value=({"id": 12}, "imdb_id")), \
                    patch.object(enrich_cast, "fetch_movie_details", return_value=(details, credits)), \
                    patch.object(enrich_cast.time, "sleep"), \
                    patch.multiple(enrich_cast, **paths):
                enrich_cast.enrich_catalog()
                enrich_cast.enrich_catalog()

            enriched = json.loads(paths["TARGET_PATH"].read_text(encoding="utf-8"))
            report = json.loads(paths["REPORT_PATH"].read_text(encoding="utf-8"))
            self.assertEqual(len(enriched), 1)
            self.assertEqual(enriched[0]["all_male_evidence"], "strong")
            self.assertEqual(enriched[0]["synopsis"], details["overview"])
            self.assertEqual(enriched[0]["runtime"], details["runtime"])
            self.assertEqual(enriched[0]["original_language"], details["original_language"])
            self.assertEqual(enriched[0]["poster_path"], details["poster_path"])
            self.assertEqual(enriched[0]["production_countries"], ["GB", "US"])
            self.assertEqual(enriched[0]["keywords"], ["The Unit", "military"])
            self.assertEqual(report["enriched_records"], 1)
            self.assertEqual(len(report["top_five_evidence"]), 1)


if __name__ == "__main__":
    unittest.main()