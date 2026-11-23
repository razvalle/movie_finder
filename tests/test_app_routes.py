"""Route checks for the minimal movie display controls.

Run from the project root:
    python tests/test_app_routes.py
"""

import sys
import re
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import COUNTRY_LABELS, app, build_match_reasons, detect_nationality, movie_poster_url
from data.generated_movies import GENERATED_MOVIES
from data.movies import MOVIES


class MinimalPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_home_page_contains_only_sort_and_layout_controls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn('id="result-sort"', html)
        self.assertIn('aria-label="Sort movies"', html)
        self.assertIn('data-view-mode="grid"', html)
        self.assertIn('data-view-mode="list"', html)
        self.assertEqual(html.count("data-view-mode="), 2)
        self.assertIn("Best match", html)
        self.assertIn("Most popular", html)
        self.assertIn("Highest rated", html)
        self.assertIn("Newest", html)

        for removed_ui in (
            "movie-card",
            "search__form",
            "data-mood-query",
            "data-open-help",
            "data-open-about",
            "movie-drawer",
            "catalog-shelves",
        ):
            self.assertNotIn(removed_ui, html)

    def test_search_and_filter_urls_still_render_only_the_minimal_page(self):
        urls = (
            "/?q=mystery+without+horror",
            "/?genre=comedy&awards=winners",
            "/?debug=1",
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'id="result-sort"', response.data)
                self.assertNotIn(b"movie-card", response.data)

    def test_minimal_styles_and_controls_script_are_served(self):
        css = self.client.get("/static/css/style.css")
        script = self.client.get("/static/js/minimal-controls.js")
        try:
            self.assertEqual(css.status_code, 200)
            self.assertIn(b".view-toggle", css.data)
            self.assertEqual(script.status_code, 200)
            self.assertIn(b"aria-pressed", script.data)
            self.assertIn(b"data-view-mode", script.data)
        finally:
            css.close()
            script.close()

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

    def test_popular_fallback_contains_real_high_vote_titles(self):
        self.assertEqual(len(GENERATED_MOVIES), 1000)
        self.assertTrue(all(movie["title"] and movie["vote_count"] >= 10_000 for movie in GENERATED_MOVIES))
        self.assertFalse(any(re.fullmatch(r".+ Story \d{4}", movie["title"]) for movie in GENERATED_MOVIES))
        self.assertTrue(any(movie.get("notable_awards") for movie in GENERATED_MOVIES))


if __name__ == "__main__":
    unittest.main()
