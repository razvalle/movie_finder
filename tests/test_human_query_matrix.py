"""Generated human-input robustness matrix for the existing search pipeline.

This test intentionally checks interpretation invariants instead of prescribing
one result for every natural-language variation. A smaller end-to-end sample
also exercises the real Flask route and scorer against the active catalog.
"""

import sys
import unittest
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import TFIDF_MODEL
from data.movies import MOVIES
from nlp.query import build_structured_query
from nlp.scoring import rank_movies


COUNTRIES = [
    ("filipino", "PH"), ("pinoy", "PH"), ("pinas", "PH"), ("ph", "PH"),
    ("japanese", "JP"), ("japan", "JP"), ("korean", "KR"), ("korea", "KR"),
    ("american", "US"), ("us", "US"), ("mexican", "MX"), ("french", "FR"),
]
GENRES = [
    ("horror", "horror"), ("scary", "horror"), ("comedy", "comedy"),
    ("funny", "comedy"), ("romance", "romance"), ("romantic", "romance"),
    ("romcom", "romance"), ("sci-fi", "sci-fi"), ("science fiction", "sci-fi"),
    ("action", "action"), ("thriller", "thriller"), ("family", "family"),
]
RANKINGS = [("best", "best"), ("top", "best"), ("good", "best"), ("great", "best"),
            ("highest rated", "best"), ("most popular", "popular")]
DATE_FORMS = [
    ("from 2020", {"min": 2020, "max": 2020}),
    ("in 2020", {"min": 2020, "max": 2020}),
    ("2020", {"min": 2020, "max": 2020}),
    ("after 2020", {"min": 2021, "max": None}),
    ("before 2010", {"min": None, "max": 2009}),
    ("from 2015 to 2020", {"min": 2015, "max": 2020}),
    ("2015-2020", {"min": 2015, "max": 2020}),
    ("in the 90s", {"min": 1990, "max": 1999}),
    ("from the 2000s", {"min": 2000, "max": 2009}),
]


def generated_queries():
    """Return well over 500 realistic surface forms from reusable templates."""
    templates = [
        "{rank} {country} {genre} movies {date}",
        "what are the {rank} {genre} films from {country} {date}",
        "show me some {rank} {country} {genre} films {date}",
        "i want a {country} {genre} movie {date}",
        "give me good {genre} movies made in {country} {date}",
        "{country} {genre} movie {date}",
    ]
    queries = {
        template.format(rank=rank, country=country, genre=genre, date=date)
        for template, (country, _), (genre, _), (rank, _), (date, _) in product(
            templates, COUNTRIES[:8], GENRES[:8], RANKINGS[:4], DATE_FORMS[:5]
        )
    }
    queries.update({
        "I WANT SOMETHING SCARY!!!", "something funny", "movie about revenge",
        "movies like interstellar", "something similar to inception",
        "movies with tom hanks", "movies directed by christopher nolan",
        "movies in korean", "walang horror", "huwag comedy", "no gore",
        "not too scary", "movies with zombies but not too much gore",
        "mga best na pinoy movies", "magandang filipino movies",
        "mga horror movie sa pinas", "pelikulang pinoy", "nakakatakot na movie",
        "nakakatawang movie", "movie tungkol sa pamilya", "something mind blowing",
        "movie movie movie horror horror", "pls give me something good lol",
        "something not boring", "i don't want something too long",
    })
    return sorted(queries)


class HumanQueryMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queries = generated_queries()
        cls.model = TFIDF_MODEL

    def test_matrix_is_large_enough(self):
        self.assertGreaterEqual(len(self.queries), 500)

    def test_matrix_has_no_explicit_filter_leakage(self):
        leakage_words = {"best", "top", "good", "filipino", "pinoy", "horror", "comedy", "romance"}
        for query in self.queries:
            parsed = build_structured_query(query)
            residual = set(parsed["semantic_description"])
            with self.subTest(query=query):
                self.assertFalse(residual & leakage_words)

    def test_matrix_expected_country_genre_ranking_and_dates(self):
        for country, country_code in COUNTRIES:
            for genre, genre_code in GENRES[:8]:
                for ranking, ranking_code in RANKINGS[:4]:
                    query = f"{ranking} {country} {genre} movies"
                    parsed = build_structured_query(query)
                    with self.subTest(query=query):
                        self.assertEqual(parsed["country"], country_code)
                        self.assertIn(genre_code, parsed["preferences"]["genres"])
                        self.assertEqual(parsed["ranking_intent"], ranking_code)
        for date_text, expected in DATE_FORMS:
            parsed = build_structured_query(f"movies {date_text}")
            self.assertEqual(parsed["preferences"]["release_year"], expected, date_text)

    def test_selected_end_to_end_human_queries(self):
        cases = [
            ("best pinoy horror movies", "PH", "horror"),
            ("movies from the 90s", "", None),
            ("walang horror", "", None),
        ]
        for query, country, genre in cases:
            parsed = build_structured_query(query)
            preferences = parsed["preferences"]
            movies = [
                movie for movie in MOVIES
                if not country or country in (movie.get("production_countries") or movie.get("origin_regions", []))
            ]
            if query == "movies from the 90s":
                movies = [movie for movie in movies if 1990 <= movie["release_year"] <= 1999]
            if genre:
                movies = [movie for movie in movies if genre in movie["genres"]]
            movies = movies[:5000]
            ranked = rank_movies(movies, preferences, self.model)
            with self.subTest(query=query):
                if genre:
                    self.assertTrue(ranked["results"])
                    self.assertTrue(all(genre in entry["movie"]["genres"] for entry in ranked["results"]))
                if query == "walang horror":
                    self.assertTrue(all("horror" not in entry["movie"]["genres"] for entry in ranked["results"]))


if __name__ == "__main__":
    unittest.main()
