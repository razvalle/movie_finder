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
from html import unescape
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    COUNTRY_LABELS,
    app,
    _contains_complete_title,
    build_match_reasons,
    detect_nationality,
    drop_constraints,
    find_title_matches,
    movie_poster_url,
    rank_movies,
)
from data.generated_movies import GENERATED_MOVIES
from data.movies import MOVIES
from nlp.explain import explain_match


class SearchRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.search_limit_patch = patch("app.allow_search", return_value=True)
        cls.search_limit_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.search_limit_patch.stop()

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
        self.assertIn(b'data-selected-genre="action"', response.data)
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
        self.assertIn(b'data-selected-genre="action"', response.data)

    def test_country_genre_and_human_input_routes(self):
        cases = [
            ("/?q=japanese+horror+movies&genre=action&nationality=IN&per_page=12&debug=1", b"Detected country</dt><dd>Japan"),
            ("/?q=a+detective+solving+a+murder+in+a+mansion&per_page=12", b"Recommended Movies"),
            ("/?q=scarry+rom+com+but+no+sad+ending+under+two+hours&per_page=12", b"Recommended Movies"),
            ("/?q=recommend+a+canadian+movie+called+incendies&per_page=12", b"Incendies"),
            ("/?q=zzzzunknownword&per_page=12", b"Closest match"),
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

    def test_natural_genre_conjunction_is_enforced_but_or_remains_alternative(self):
        movies = [
            {
                "id": 990001, "title": "Both Genres", "synopsis": "A story.",
                "genres": ["comedy", "drama"], "release_year": 2020, "runtime": 100,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "average_rating": 7.0, "vote_count": 1000, "notable_awards": [],
                "origin_regions": [],
            },
            {
                "id": 990002, "title": "Comedy Only", "synopsis": "A story.",
                "genres": ["comedy"], "release_year": 2020, "runtime": 100,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "average_rating": 7.0, "vote_count": 1000, "notable_awards": [],
                "origin_regions": [],
            },
            {
                "id": 990003, "title": "Drama Only", "synopsis": "A story.",
                "genres": ["drama"], "release_year": 2020, "runtime": 100,
                "themes": [], "mood_tags": [], "keywords": [], "content_descriptors": [],
                "average_rating": 7.0, "vote_count": 1000, "notable_awards": [],
                "origin_regions": [],
            },
        ]
        with patch("app.MOVIES", movies):
            conjunction = self.client.get("/?q=comedy+and+drama+movies&per_page=48")
            alternatives = self.client.get("/?q=comedy+or+drama+movies&per_page=48")

        self.assertIn(b"Both Genres", conjunction.data)
        self.assertNotIn(b"Comedy Only", conjunction.data)
        self.assertNotIn(b"Drama Only", conjunction.data)
        self.assertIn(b"Both Genres", alternatives.data)
        self.assertIn(b"Comedy Only", alternatives.data)
        self.assertIn(b"Drama Only", alternatives.data)

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

    def test_genre_and_recognition_filters_combine_and_rating_is_card_metadata(self):
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
            response = self.client.get("/?genre=drama&awards=winners&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"English Film", response.data)
        self.assertIn(b"Spanish Film", response.data)
        self.assertNotIn(b"Spanish Action Film", response.data)
        self.assertIn(b"Age rating", response.data)
        self.assertIn(b"<span class=\"movie-age-rating__value\">R</span>", response.data)
        self.assertIn(b"United Kingdom", response.data)
        self.assertIn(b"https://image.tmdb.org/t/p/w342/spanish.jpg", response.data)

    def test_home_page_does_not_render_match_count_without_a_search(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('data-result-count=', html)
        self.assertIn(f'Showing 1-12 of {len(MOVIES)}', html)
        self.assertIn('All Movies', html)

    def test_home_page_does_not_render_curated_movie_shelves(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("data-catalog-shelves", html)
        self.assertNotIn("Top rated", html)
        self.assertNotIn("Under 100 minutes", html)
        self.assertNotIn("90s classics", html)

    def test_home_page_omits_mood_picks_and_keeps_search_controls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("Pick a mood", html)
        self.assertNotIn("data-mood-query", html)
        self.assertIn('id="q"', html)
        self.assertIn('id="result-sort"', html)
        self.assertIn('data-view-mode="grid"', html)

    def test_hard_runtime_and_year_filters_are_not_relaxed(self):
        movies = deepcopy(MOVIES[:3])
        for movie in movies:
            movie["runtime"] = 90
            movie["release_year"] = 2020
            movie["genres"] = ["drama"]
        with patch("app.MOVIES", movies), patch("app.allow_search", return_value=True):
            runtime_response = self.client.get("/?q=movies+under+30+minutes&per_page=48")
            year_response = self.client.get("/?q=movies+after+2026&per_page=48")
        self.assertEqual(runtime_response.status_code, 200)
        self.assertEqual(year_response.status_code, 200)
        self.assertNotIn(b'data-detail-title=', runtime_response.data)
        self.assertNotIn(b'data-detail-title=', year_response.data)
        self.assertIn(b"No close matches found", runtime_response.data)
        self.assertIn(b"No close matches found", year_response.data)

    def test_overlong_query_is_capped_with_a_next_step(self):
        query = "space " * 2000
        response = self.client.get("/?q=" + quote(query))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"first 500 characters", response.data)

    def test_retrieval_exception_degrades_to_filtered_closest_matches(self):
        with (
            patch("app.retrieve_candidates", side_effect=RuntimeError("retrieval failed")),
            patch("app.write_search_record") as write_record,
        ):
            response = self.client.get("/?q=mystery+movies&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Closest match", response.data)
        self.assertNotIn(b"Exact match", response.data)
        record = write_record.call_args.args[0]
        self.assertIn("expand-retrieve", [item["stage"] for item in record["errors"]])
        self.assertGreater(record["candidate_counts"]["filtered"], 0)
        self.assertNotIn("remote_addr", record)

    def test_explanation_exception_keeps_results_and_supplies_a_fallback_reason(self):
        with patch("app.build_match_reasons", side_effect=RuntimeError("explanation failed")):
            response = self.client.get("/?q=space+movies&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="movie-card__reasons"', response.data)

    def test_exact_label_is_downgraded_when_the_displayed_score_is_below_threshold(self):
        original_rank = rank_movies

        def lower_scores(*args, **kwargs):
            ranking = original_rank(*args, **kwargs)
            for entry in ranking["results"]:
                entry["percent"] = 20
            return ranking

        with patch("app.rank_movies", side_effect=lower_scores):
            response = self.client.get("/?q=The+Godfather&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"20%", response.data)
        self.assertNotIn(b"Exact match", response.data)

    def test_template_exception_returns_a_clear_non_5xx_response(self):
        with patch("app.render_template", side_effect=RuntimeError("render failed")):
            response = self.client.get("/?q=space+movies")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Search could not safely prepare results", response.data)

    def test_adversarial_query_and_pagination_inputs_do_not_crash(self):
        cases = (
            "   ", "x", "😀🎬🍿", "<script>alert(1)</script>",
            "' OR 1=1 --", "under 999999999999999999 minutes",
            "movies after -100", "hello 世界 مرحبا", "comedy " * 200,
        )
        for query in cases:
            with self.subTest(query=query[:40]):
                response = self.client.get("/?q=" + quote(query) + "&page=-1&per_page=invalid")
                self.assertLess(response.status_code, 500)
        markup = self.client.get("/?q=" + quote("<script>alert(1)</script>"))
        self.assertNotIn(b"<script>alert(1)</script>", markup.data)

    def test_exact_and_leading_article_title_queries_return_only_the_title(self):
        for query, expected_title in (
            ("Get Out", "Get Out"),
            ("the Chef", "Chef"),
            ("warm bodies", "Warm Bodies"),
            ("WALL E", "WALL·E"),
            ("Amélie", "Amélie"),
            ("the Us", "Us"),
            ("the It", "It"),
            ("the Lion", "Lion"),
            ("the Twilight", "Twilight"),
            ("the Amelie", "Amelie"),
            ("the Amélie", "Amélie"),
            ("Amelie under 123 minutes", "Amelie"),
            ("The Thing under 110 minutes", "The Thing"),
            ("The Hangover under 101 minutes", "The Hangover"),
            ("The Matrix under 137 minutes", "The Matrix"),
            ("The Avengers under 144 minutes", "The Avengers"),
            ("Get Out under 105 minutes", "Get Out"),
            ("American History X", "American History X"),
            ("the American Beauty", "American Beauty"),
            ("No Country for Old Men", "No Country for Old Men"),
            ("the No Country for Old Men", "No Country for Old Men"),
            ("Zodiac under 158 minutes", "Zodiac"),
            ("What s Eating Gilbert Grape", "What's Eating Gilbert Grape"),
        ):
            with self.subTest(query=query):
                response = self.client.get("/?q=" + quote(query) + "&per_page=48")
                titles = [
                    unescape(title)
                    for title in re.findall(
                        r'data-detail-title="([^"]+)"',
                        response.get_data(as_text=True),
                    )
                ]
                self.assertEqual(titles, [expected_title])

    def test_title_prefix_matches_partial_final_word_without_shorter_aliases(self):
        response = self.client.get("/?q=The+Lion+K&per_page=48")
        titles = re.findall(
            r'data-detail-title="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIn("The Lion King", titles)
        self.assertNotIn("Lion", titles)
        self.assertIn(b'data-i18n="closestMatch"', response.data)
        self.assertIn(
            "Scary Movie",
            [movie["title"] for movie in find_title_matches("scary movie movies", MOVIES)],
        )

    def test_title_evidence_requires_the_complete_title_phrase(self):
        self.assertTrue(_contains_complete_title(
            "Synthetic Orchard under 100 minutes", "Synthetic Orchard",
        ))
        self.assertFalse(_contains_complete_title(
            "Synthetic Orch", "Synthetic Orchard",
        ))

    def test_unsupported_cast_and_certification_filters_are_explained(self):
        for query in ("movies starring a person", "movies rated PG-13"):
            with self.subTest(query=query):
                response = self.client.get("/?q=" + quote(query))
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"catalog does not include", response.data)
                self.assertNotIn(b'data-detail-title=', response.data)

    def test_dropping_runtime_chip_clears_verification_filter(self):
        preferences = {
            "runtime": {"min": None, "max": 100, "target": None, "tolerance": 15},
        }
        understanding = {
            "filters": {
                "runtime": {"min": None, "max": 100, "target": None},
            },
            "hard_constraints": [],
        }
        drop_constraints(preferences, understanding, ["runtime"])
        self.assertIsNone(preferences["runtime"]["max"])
        self.assertEqual(
            understanding["filters"]["runtime"],
            {"min": None, "max": None, "target": None},
        )

    def test_ui_has_language_control_with_english_default_and_theme_toggle(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<a class="site-header__home" href="/" aria-label="Movie Finder home">', html)
        self.assertIn('id="ui-language"', html)
        self.assertIn('data-clear-filters', html)
        self.assertIn('id="genre-menu-search"', html)
        self.assertIn('id="recent-searches"', html)
        self.assertIn('id="result-sort"', html)
        self.assertNotIn('data-result-count=', html)
        self.assertIn('<option value="en" lang="en" selected>English</option>', html)
        self.assertIn('<option value="es" lang="es">Español</option>', html)
        self.assertIn('<option value="tl" lang="tl">Tagalog</option>', html)
        self.assertIn('<option value="fr" lang="fr">Français</option>', html)
        self.assertIn('<option value="de" lang="de">Deutsch</option>', html)
        self.assertIn('<option value="pt" lang="pt">Português</option>', html)
        self.assertIn('<option value="ja" lang="ja">日本語</option>', html)
        self.assertIn('<option value="ko" lang="ko">한국어</option>', html)
        self.assertIn('<option value="zh" lang="zh">中文</option>', html)
        self.assertNotIn('filter-add-button', html)
        self.assertNotIn('active-filter-chips', html)
        self.assertIn('id="theme-toggle"', html)

    def test_selected_genres_render_as_removable_chips(self):
        response = self.client.get("/?genre=comedy&genre=drama")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('class="search__entry"', html)
        self.assertIn('data-genre-chips', html)
        self.assertIn('data-genre-menu', html)
        self.assertIn('data-add-genre="action"', html)
        self.assertIn('data-selected-genre="comedy"', html)
        self.assertIn('data-selected-genre="drama"', html)
        self.assertIn('type="hidden" name="genre" value="comedy"', html)
        self.assertIn('type="hidden" name="genre" value="drama"', html)
        self.assertIn('data-remove-genre="comedy"', html)
        self.assertIn('data-remove-genre="drama"', html)
        self.assertIn('data-add-genre="comedy"', html)
        self.assertIn('data-add-genre="drama"', html)
        self.assertIn('aria-label="Remove Comedy genre"', html)
        self.assertIn('aria-label="Remove Drama genre"', html)

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

    def test_imported_original_title_is_shown_only_when_distinct(self):
        movie = {
            "id": 999997, "title": "Display Title", "original_title": "Titre d'origine",
            "synopsis": "A drama.", "genres": ["drama"], "runtime": 90,
            "release_year": 2020, "themes": [], "mood_tags": [], "keywords": [],
            "content_descriptors": [], "notable_awards": [], "origin_regions": [],
            "average_rating": 7.0, "vote_count": 100,
        }
        with patch("app.MOVIES", [movie]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Original title", response.data)
        self.assertIn(b"Titre d&#39;origine", response.data)

        movie["original_title"] = movie["title"]
        with patch("app.MOVIES", [movie]):
            response = self.client.get("/")
        self.assertNotIn(b"Original title", response.data)

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

    def test_language_query_is_ignored_in_english_only_interface(self):
        response = self.client.get("/?language=es")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"catalog doesn&#39;t include language details yet", response.data)
        self.assertNotIn(b'name="language"', response.data)
        self.assertNotIn(b"Missing or unverified", response.data)
        self.assertIn(b"All Movies", response.data)

    def test_negated_query_does_not_match_on_generic_title_words(self):
        response = self.client.get("/?q=a+movie+where+there+is+no+woman&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"We couldn&#39;t find a movie that exactly matches your request", response.data)
        self.assertIn(b"0 exact, 30 close, 0 related", response.data)
        self.assertIn(b"Unverified", response.data)
        self.assertIn(b"MOVIE_FINDER_GROUP4", response.data)
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
        self.assertIn(b"0 exact, 0 close, 0 related", response.data)
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
