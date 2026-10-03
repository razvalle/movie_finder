"""Flask route regression checks for human search interactions.

Run from the project root:
    python tests/test_app_routes.py
    python -m unittest tests.test_app_routes.SearchRouteTests.test_search_benchmark_expected_top_results
"""

import sys
import unittest
import re
import sqlite3
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import COUNTRY_LABELS, app, build_match_reasons, detect_nationality, movie_poster_url
from data.generated_movies import GENERATED_MOVIES
from data.movies import MOVIES
from nlp.explain import explain_match


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

    def test_genre_recognition_and_language_filters_combine_and_rating_is_card_metadata(self):
        movies = [
            {
                "id": 999991, "title": "English Film", "synopsis": "English record.",
                "genres": ["drama"], "runtime": 90, "release_year": 2020,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "original_language": "en", "certification": "PG", "average_rating": 7.0,
                "vote_count": 100, "notable_awards": ["Award"], "origin_regions": ["US"], "nationality": "US",
                "production_countries": ["US"], "poster_path": "/english.jpg",
            },
            {
                "id": 999992, "title": "Spanish Film", "synopsis": "Spanish record.",
                "genres": ["drama"], "runtime": 90, "release_year": 2020,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "original_language": "es", "certification": "R", "average_rating": 7.0,
                "vote_count": 100, "notable_awards": ["Award"], "origin_regions": ["ES"], "nationality": "ES",
                "production_countries": ["GB"], "poster_path": "/spanish.jpg",
            },
            {
                "id": 999993, "title": "Spanish Action Film", "synopsis": "Spanish action record.",
                "genres": ["action"], "runtime": 90, "release_year": 2020,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "original_language": "es", "certification": "R", "average_rating": 7.0,
                "vote_count": 100, "notable_awards": ["Award"], "origin_regions": ["ES"], "nationality": "ES",
            },
        ]
        with patch("app.MOVIES", movies):
            response = self.client.get("/?genre=drama&awards=winners&language=es&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Spanish Film", response.data)
        self.assertNotIn(b"English Film", response.data)
        self.assertNotIn(b"Spanish Action Film", response.data)
        self.assertIn(b'value="es" lang="es" selected', response.data)
        self.assertIn(b"Age rating", response.data)
        self.assertIn(b"<span class=\"movie-age-rating__value\">R</span>", response.data)
        self.assertIn(b"United Kingdom", response.data)
        self.assertIn(b"https://image.tmdb.org/t/p/w342/spanish.jpg", response.data)

    def test_manual_show_movies_button_is_removed_and_controls_exist(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Show Movies", response.data)
        self.assertIn(b'name="language"', response.data)
        self.assertNotIn(b'name="age_rating"', response.data)
        self.assertEqual(response.data.count(b'id="ui-language"'), 1)
        self.assertNotIn(b'id="language"', response.data)
        self.assertIn(b'value="tl" lang="tl"', response.data)
        self.assertIn(b'id="theme-toggle"', response.data)

    def test_age_rating_never_filters_and_only_appears_per_movie(self):
        movies = [
            {
                "id": 999994, "title": "PG Film", "synopsis": "PG record.", "genres": ["drama"],
                "runtime": 90, "release_year": 2020, "themes": [], "mood_tags": [], "keywords": [],
                "content_descriptors": [], "certification": "PG", "notable_awards": [],
                "origin_regions": [], "average_rating": 7.0, "vote_count": 100,
            },
            {
                "id": 999995, "title": "R Film", "synopsis": "R record.", "genres": ["drama"],
                "runtime": 90, "release_year": 2020, "themes": [], "mood_tags": [], "keywords": [],
                "content_descriptors": [], "certification": "R", "notable_awards": [],
                "origin_regions": [], "average_rating": 7.0, "vote_count": 100,
            },
        ]
        with patch("app.MOVIES", movies):
            response = self.client.get("/?age_rating=R")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PG Film", response.data)
        self.assertIn(b"R Film", response.data)
        self.assertIn(b'<span class="movie-age-rating__value">PG</span>', response.data)
        self.assertIn(b'<span class="movie-age-rating__value">R</span>', response.data)
        self.assertNotIn(b'name="age_rating"', response.data)

    def test_release_regions_are_not_displayed_as_movie_origin(self):
        movie = {
            "id": 999996, "title": "Origin Unknown", "synopsis": "A drama.", "genres": ["drama"],
            "runtime": 90, "release_year": 2020, "themes": [], "mood_tags": [], "keywords": [],
            "content_descriptors": [], "origin_regions": ["AE"], "nationality": "AE",
            "notable_awards": [], "average_rating": 7.0, "vote_count": 100,
        }
        with patch("app.MOVIES", [movie]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Origin unavailable", response.data)
        self.assertNotIn(b"United Arab Emirates", response.data)

    def test_commons_poster_url_is_movie_specific_and_host_allowlisted(self):
        commons_url = "https://commons.wikimedia.org/wiki/Special:FilePath/Film_Poster.jpg"
        self.assertEqual(movie_poster_url({"poster_url": commons_url}), commons_url)
        self.assertEqual(movie_poster_url({"poster_url": "https://example.invalid/poster.jpg"}), "")

    def test_search_explanations_use_existing_scoring_breakdown(self):
        movie = {"runtime": 110, "release_year": 2020}
        preferences = {
            "runtime": {"min": None, "max": None, "target": None},
            "release_year": {"min": 2020, "max": 2020},
            "excluded_genres": ["horror"],
            "excluded_moods": [],
            "excluded_themes": [],
        }
        breakdown = {
            "genre": {"matched": ["mystery"], "missing": []},
            "mood": {"matched": ["suspenseful"], "missing": []},
            "theme": None,
            "keyword": None,
            "runtime": None,
            "release_year": 1.0,
        }
        reasons = build_match_reasons(
            movie,
            preferences,
            breakdown,
            ["mood suspenseful", "theme mystery", "keyword detective"],
            cast_supported=True,
        )
        reason_text = [reason["text"] for reason in reasons]
        self.assertIn("Mystery genre matched", reason_text)
        self.assertIn("Suspenseful mood matched", reason_text)
        self.assertIn("Released in 2020", reason_text)
        self.assertIn("No horror tag detected", reason_text)
        self.assertIn("Matched: mood suspenseful", reason_text)
        self.assertIn("Matched: theme mystery", reason_text)
        self.assertIn("Matched: keyword detective", reason_text)
        self.assertIn("Cast evidence supports your request", reason_text)

    def test_search_benchmark_expected_top_results(self):
        cases = [
            ("The Ring", "The Ring"),
            ("Dunkirk", "Dunkirk"),
            ("recommend a canadian movie called Incendies", "Incendies"),
        ]
        for query, expected_title in cases:
            with self.subTest(query=query):
                response = self.client.get("/", query_string={"q": query, "per_page": 12})
                self.assertEqual(response.status_code, 200)
                titles = re.findall(rb'<h3 class="movie-card__title">(.*?)</h3>', response.data)
                self.assertTrue(titles)
                self.assertEqual(titles[0].decode("utf-8"), expected_title)

    def test_missing_posters_render_fixed_fallback_asset(self):
        movie = deepcopy(MOVIES[0])
        movie["id"] = 99999991
        movie.pop("poster_url", None)
        movie.pop("poster_path", None)
        with patch("app.MOVIES", [movie]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="/static/poster-placeholder.svg"', response.data)
        self.assertIn(b'data-poster-fallback="/static/poster-placeholder.svg"', response.data)

    def test_feedback_submission_is_persisted_without_changing_search(self):
        movie = MOVIES[0]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "feedback.sqlite3"
            with patch("app.FEEDBACK_DB_PATH", database):
                response = self.client.post("/feedback", data={
                    "query": "mystery without horror",
                    "movie_id": str(movie["id"]),
                    "relevant": "0",
                })
            self.assertEqual(response.status_code, 302)
            self.assertIn("feedback=saved", response.headers["Location"])
            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT query, movie_id, movie_title, relevant, created_at FROM result_feedback"
                ).fetchone()
            finally:
                connection.close()
        self.assertEqual(row[:4], ("mystery without horror", movie["id"], movie["title"], 0))
        self.assertTrue(row[4].endswith("+00:00"))

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
