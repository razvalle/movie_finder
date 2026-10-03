"""Hand-authored search oracle over the assembled compact movie catalog.

Expected titles and constraints are checked against the catalog records below.
This is an oracle for the current rule-based pipeline, not GPT-era ground truth
and not a copy of the old Render fallback benchmark.
"""

import ast
import re
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import app
from data.generated_movies import GENERATED_MOVIES
from nlp.normalize import normalize_text
from nlp.query import build_structured_query
from nlp.smart_search import understand_query


ROOT_DIR = Path(__file__).resolve().parents[1]
GOLDEN_CASES = [
    {
        "query": "90s comedy with a road trip",
        "title": "Dumb and Dumber",
        "genres": {"comedy"},
        "year_range": (1990, 1999),
        "hard_constraints": {"road trip", "release year"},
        "unverified": "road trip",
    },
    {
        "query": "animated movie about a rat who cooks",
        "title": "Ratatouille",
        "genres": {"animation"},
        "catalog_evidence": {"rat", "chef", "cook"},
    },
    {
        "query": "Groundhog Day 1993 comedy time loop",
        "title": "Groundhog Day",
        "genres": {"comedy"},
        "themes": {"time loop"},
        "year_range": (1993, 1993),
    },
    {
        "query": "Alien 1979 space horror",
        "title": "Alien",
        "genres": {"horror"},
        "themes": {"space exploration"},
        "year_range": (1979, 1979),
    },
    {
        "query": "The Dark Knight 2008 dark action",
        "title": "The Dark Knight",
        "genres": {"action"},
        "moods": {"dark"},
        "year_range": (2008, 2008),
    },
    {
        "query": "Searching for Bobby Fischer chess drama",
        "title": "Searching for Bobby Fischer",
        "genres": {"drama"},
    },
    {
        "query": "The Matrix 1999 sci-fi action, no comedy",
        "title": "The Matrix",
        "genres": {"action", "sci-fi"},
        "excluded_genres": {"comedy"},
        "year_range": (1999, 1999),
    },
    {
        "query": "The Lord of the Rings: The Fellowship of the Ring 2001 fantasy adventure",
        "title": "The Lord of the Rings: The Fellowship of the Ring",
        "genres": {"adventure", "fantasy"},
        "year_range": (2001, 2001),
    },
    {
        "query": "Toy Story 1995 animation family comedy",
        "title": "Toy Story",
        "genres": {"animation", "family", "comedy"},
        "year_range": (1995, 1995),
    },
    {
        "query": "The Silence of the Lambs 1991 horror crime, no comedy",
        "title": "The Silence of the Lambs",
        "genres": {"horror", "crime"},
        "excluded_genres": {"comedy"},
        "year_range": (1991, 1991),
    },
]


def deployment_catalog():
    source = ast.parse((ROOT_DIR / "data" / "movies.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MOVIES" for target in node.targets)
    )
    return ast.literal_eval(assignment.value) + deepcopy(GENERATED_MOVIES)


def title_indexes(movies):
    exact_index = {}
    token_index = {}
    for movie in movies:
        normalized_title = normalize_text(movie["title"]).replace(",", " ").strip()
        exact_index.setdefault(normalized_title, []).append(movie)
        for token in set(normalized_title.split()):
            if len(token) >= 4:
                token_index.setdefault(token, []).append(movie)
    return exact_index, token_index


class SearchGoldenTests(unittest.TestCase):
    def test_hand_authored_catalog_search_oracle(self):
        movies = deployment_catalog()
        exact_index, token_index = title_indexes(movies)
        catalog_by_title = {movie["title"]: movie for movie in movies}
        client = app.test_client()

        with (
            patch("app.MOVIES", movies),
            patch("app.TITLE_INDEX", exact_index),
            patch("app.TITLE_TOKEN_INDEX", token_index),
        ):
            for case in GOLDEN_CASES:
                with self.subTest(query=case["query"]):
                    movie = catalog_by_title[case["title"]]
                    self.assertTrue(case["genres"].issubset(set(movie["genres"])))
                    if "year_range" in case:
                        lower, upper = case["year_range"]
                        self.assertLessEqual(lower, movie["release_year"])
                        self.assertLessEqual(movie["release_year"], upper)
                    if "catalog_evidence" in case:
                        searchable = normalize_text(" ".join((
                            movie.get("title", ""),
                            movie.get("synopsis", ""),
                            *movie.get("themes", []),
                            *movie.get("keywords", []),
                        )))
                        for term in case["catalog_evidence"]:
                            self.assertIn(term, searchable)

                    preferences = build_structured_query(case["query"])["preferences"]
                    self.assertTrue(case["genres"].issubset(set(preferences["genres"])))
                    self.assertTrue(case.get("themes", set()).issubset(set(preferences["themes"])))
                    self.assertTrue(case.get("moods", set()).issubset(set(preferences["moods"])))
                    self.assertTrue(case.get("excluded_genres", set()).issubset(set(preferences["excluded_genres"])))
                    if "year_range" in case:
                        lower, upper = case["year_range"]
                        parsed_year = preferences["release_year"]
                        self.assertEqual((parsed_year["min"], parsed_year["max"]), (lower, upper))

                    response = client.get("/", query_string={"q": case["query"], "per_page": 12})
                    self.assertEqual(response.status_code, 200)
                    titles = re.findall(rb'<h3 class="movie-card__title">(.*?)</h3>', response.data)
                    self.assertEqual([title.decode("utf-8") for title in titles], [case["title"]])

                    if "hard_constraints" in case:
                        understanding = understand_query(case["query"])
                        self.assertTrue(case["hard_constraints"].issubset(set(understanding["hard_constraints"])))
                    if "unverified" in case:
                        self.assertIn(b"Unverified", response.data)
                        self.assertIn(case["unverified"].encode("utf-8"), response.data)


if __name__ == "__main__":
    unittest.main()