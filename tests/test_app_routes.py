"""Flask route regression checks for human search interactions.

Run from the project root:
    python tests/test_app_routes.py
"""

import sys
import unittest
import re
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import COUNTRY_LABELS, app, detect_nationality
from data.generated_movies import GENERATED_MOVIES
from data.movies import MOVIES


class SearchRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_all_available_country_names_resolve(self):
        codes = sorted({
            code
            for movie in MOVIES
            for code in movie.get("origin_regions", [])
            if code in COUNTRY_LABELS
        })
        failures = [
            (code, COUNTRY_LABELS[code], detect_nationality(f"movies from {COUNTRY_LABELS[code]}"))
            for code in codes
            if detect_nationality(f"movies from {COUNTRY_LABELS[code]}") != code
        ]
        self.assertEqual(failures, [])

    def test_explicit_genre_filter_overrides_title_query(self):
        response = self.client.get(
            "/?q=recommend+the+grand+budapest+hotel&genre=action&nationality=CA&per_page=12"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="action" selected', response.data)
        self.assertNotIn(b"The Grand Budapest Hotel", response.data)

    def test_country_overrides_stale_origin(self):
        response = self.client.get("/?q=movies+from+cabo+verde&nationality=IN&per_page=12&debug=1")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'id="nationality"', response.data)
        self.assertIn(b"Detected country</dt><dd>Cabo Verde", response.data)

    def test_explicit_genre_filter_overrides_query_genre(self):
        response = self.client.get("/?q=japanese+horror+movies&genre=action&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"The selected genre filter overrides", response.data)
        self.assertIn(b'value="action" selected', response.data)

    def test_country_genre_and_human_input_routes(self):
        cases = [
            ("/?q=japanese+horror+movies&genre=action&nationality=IN&per_page=12&debug=1", b"Detected country</dt><dd>Japan"),
            ("/?q=a+detective+solving+a+murder+in+a+mansion&per_page=12", b"Recommended Movies"),
            ("/?q=scarry+rom+com+but+no+sad+ending+under+two+hours&per_page=12", b"Recommended Movies"),
            ("/?q=recommend+a+canadian+movie+called+incendies&per_page=12", b"Incendies"),
            ("/?q=zzzzunknownword&per_page=12", b"No close matches found"),
        ]
        for url, marker in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.data)

    def test_debug_panel_shows_structured_interpretation(self):
        response = self.client.get(
            "/?q=whats+the+best+ph+horror+movies+from+2020&debug=1&per_page=12"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Query Debug", response.data)
        self.assertIn(b"what is the best philippines horror movies from 2020", response.data)
        self.assertIn(b"Philippines", response.data)
        self.assertIn(b"Detected intent</dt><dd>best", response.data)

    def test_query_year_is_applied_before_results_are_rendered(self):
        response = self.client.get("/?q=movies+from+2015+to+2020&per_page=48")
        self.assertEqual(response.status_code, 200)
        years = [int(value) for value in re.findall(rb"\xc2\xb7 (\d{4})</div>", response.data)]
        self.assertTrue(years)
        self.assertTrue(all(2015 <= year <= 2020 for year in years))

    def test_debug_panel_shows_negative_filters(self):
        response = self.client.get("/?q=filipino+horror+movies+without+comedy&debug=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Negative filters", response.data)
        self.assertIn(b"Comedy", response.data)

    def test_award_filter_shows_recognized_movies(self):
        response = self.client.get("/?awards=winners&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="winners" selected', response.data)
        self.assertIn(b"Academy Award", response.data)
        self.assertNotIn(b"Story 0009", response.data)

    def test_language_and_age_rating_filters_apply_together(self):
        movies = [
            {
                "id": 999991, "title": "English Film", "synopsis": "English record.",
                "genres": ["drama"], "runtime": 90, "release_year": 2020,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "original_language": "en", "certification": "PG", "average_rating": 7.0,
                "vote_count": 100, "notable_awards": [], "origin_regions": ["US"], "nationality": "US",
            },
            {
                "id": 999992, "title": "Spanish Film", "synopsis": "Spanish record.",
                "genres": ["drama"], "runtime": 90, "release_year": 2020,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "original_language": "es", "certification": "R", "average_rating": 7.0,
                "vote_count": 100, "notable_awards": [], "origin_regions": ["ES"], "nationality": "ES",
            },
        ]
        with patch("app.MOVIES", movies):
            response = self.client.get("/?language=es&age_rating=R")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Spanish Film", response.data)
        self.assertNotIn(b"English Film", response.data)
        self.assertIn(b'value="es" selected', response.data)
        self.assertIn(b'value="R" selected', response.data)

    def test_manual_show_movies_button_is_removed_and_controls_exist(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Show Movies", response.data)
        self.assertIn(b'name="language"', response.data)
        self.assertIn(b'name="age_rating"', response.data)
        self.assertIn(b'id="theme-toggle"', response.data)

    def test_unavailable_filter_offers_alternative_searches(self):
        response = self.client.get("/?language=es")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"catalog doesn&#39;t include language details yet", response.data)
        self.assertIn(b'class="search-alternatives"', response.data)
        self.assertNotIn(b"Missing or unverified", response.data)

    def test_negated_query_does_not_match_on_generic_title_words(self):
        response = self.client.get("/?q=a+movie+where+there+is+no+woman&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"We couldn&#39;t find a movie that exactly matches your request", response.data)
        self.assertIn(b"30 closest matches", response.data)
        self.assertIn(b"Unverified", response.data)
        self.assertIn(b"MOVIE_FINDER_GROUP4", response.data)
        self.assertIn(b"not endorsed or certified by TMDB", response.data)
        self.assertNotIn(b"Where There Is Life", response.data)
        self.assertNotIn(b"There Will Be Blood", response.data)
        self.assertNotIn(b"There's Something About Mary", response.data)
        self.assertNotIn(b"Related to there", response.data)

    def test_no_women_query_shows_conflicting_alternative_when_none_pass(self):
        movie = {
            "id": 999999,
            "title": "Contradicted Film",
            "synopsis": "A woman leads a family through danger.",
            "genres": ["drama"],
            "runtime": 100,
            "release_year": 2020,
            "themes": [],
            "mood_tags": [],
            "keywords": ["family"],
            "content_descriptors": [],
            "average_rating": 7.0,
            "vote_count": 1000,
            "notable_awards": [],
            "origin_regions": [],
        }
        with patch("app.MOVIES", [movie]):
            response = self.client.get("/?q=a+movie+where+there+is+no+woman&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"0 closest matches", response.data)
        self.assertNotIn(b"Contradicted Film", response.data)

    def test_female_lead_query_renders_positive_cast_evidence(self):
        movie = {
            "id": 999998,
            "title": "The Lead",
            "synopsis": "A detective investigates a theft.",
            "genres": ["drama"],
            "runtime": 100,
            "release_year": 2020,
            "themes": [],
            "mood_tags": [],
            "keywords": ["detective"],
            "content_descriptors": [],
            "average_rating": 7.0,
            "vote_count": 1000,
            "notable_awards": [],
            "origin_regions": [],
            "cast": [
                {"name": "Avery", "character": "Lead", "gender": 1},
                {"name": "Blake", "character": "Detective", "gender": 2},
                {"name": "Casey", "character": "Witness", "gender": 2},
            ],
        }
        with patch("app.MOVIES", [movie]):
            response = self.client.get("/?q=a+movie+with+a+female+lead&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"The Lead", response.data)
        self.assertIn(b"Evidence found", response.data)
        self.assertNotIn(b"Contradicted</strong>", response.data)

    def test_popular_fallback_contains_real_high_vote_titles(self):
        self.assertEqual(len(GENERATED_MOVIES), 1000)
        self.assertTrue(all(movie["title"] and movie["vote_count"] >= 10_000 for movie in GENERATED_MOVIES))
        self.assertFalse(any(re.fullmatch(r".+ Story \d{4}", movie["title"]) for movie in GENERATED_MOVIES))
        self.assertTrue(any(movie.get("notable_awards") for movie in GENERATED_MOVIES))


if __name__ == "__main__":
    unittest.main()
