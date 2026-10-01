"""Tests for the no-key public movie metadata fallback."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.enrich_public_metadata import (
    build_wikidata_query,
    build_wikidata_title_query,
    poster_fields,
    records_from_bindings,
    records_from_title_bindings,
)


class PublicMetadataTests(unittest.TestCase):
    def test_wikidata_query_batches_imdb_identifiers(self):
        query = build_wikidata_query(["tt0137523", "tt0111161"])
        self.assertIn('"tt0137523"', query)
        self.assertIn('"tt0111161"', query)
        self.assertIn("wdt:P495", query)
        self.assertIn("wdt:P18", query)

    def test_only_commons_poster_images_are_used(self):
        poster = poster_fields("http://commons.wikimedia.org/wiki/Special:FilePath/Film_Poster.jpg")
        self.assertTrue(poster["poster_url"].startswith("https://commons.wikimedia.org/"))
        self.assertIn("/wiki/File:Film_Poster.jpg", poster["poster_source_url"])
        self.assertEqual(poster_fields("https://example.com/Film_Poster.jpg"), {})
        self.assertEqual(poster_fields("https://commons.wikimedia.org/wiki/Special:FilePath/Film_Still.jpg"), {})

    def test_imdb_join_returns_country_and_movie_poster_for_matching_record(self):
        movies = [{"id": 1, "imdb_id": "tt0137523", "title": "Fight Club"}]
        bindings = [
            {
                "imdbID": {"value": "tt0137523"},
                "countryCode": {"value": "US"},
                "image": {"value": "https://commons.wikimedia.org/wiki/Special:FilePath/Fight_Club_Poster.jpg"},
            },
            {"imdbID": {"value": "tt0000000"}, "countryCode": {"value": "GB"}},
        ]
        records = records_from_bindings(movies, bindings)
        self.assertEqual(records["tt0137523"]["production_countries"], ["US"])
        self.assertEqual(records["tt0137523"]["country_source"], "Wikidata property P495")
        self.assertTrue(records["tt0137523"]["poster_url"].endswith("Fight_Club_Poster.jpg"))
        self.assertEqual(len(records), 1)

    def test_title_year_join_covers_curated_movies_without_imdb_ids(self):
        movies = [{"id": 1, "title": "Inception", "release_year": 2010}]
        query = build_wikidata_title_query(movies)
        self.assertIn('"Inception" 2010', query)
        self.assertIn("wdt:P577", query)
        bindings = [{
            "requestedTitle": {"value": "Inception"},
            "requestedYear": {"value": "2010"},
            "countryCode": {"value": "US"},
            "image": {"value": "https://commons.wikimedia.org/wiki/Special:FilePath/Inception_Cast.jpg"},
        }]
        records = records_from_title_bindings(movies, bindings)
        self.assertEqual(records["id:1"]["production_countries"], ["US"])
        self.assertNotIn("poster_url", records["id:1"])


if __name__ == "__main__":
    unittest.main()
