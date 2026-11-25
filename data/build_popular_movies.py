"""Build the compact popular fallback catalog from the local IMDb export."""

import ast
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.award_winners import annotate_awards
from data.audit_catalog import clean_catalog, write_catalog_audit_log


DATA_DIR = Path(__file__).resolve().parent
SOURCE_PATH = DATA_DIR / "imdb_movies.json"
OUTPUT_PATH = DATA_DIR / "popular_movies.json"
POPULAR_MOVIE_COUNT = 1000
MINIMUM_VOTES = 10_000
PINNED_TITLES = [
    ("queen of katwe", 2016),
    ("groundhog day", 1993),
    ("dumb and dumber", 1994),
    ("tommy boy", 1995),
    ("event horizon", 1997),
    ("searching for bobby fischer", 1993),
]


def normalized_title(title):
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def core_titles():
    source = ast.parse((DATA_DIR / "movies.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MOVIES" for target in node.targets)
    )
    return {
        (
            normalized_title(movie["title"]),
            movie["release_year"],
            movie.get("runtime"),
        )
        for movie in ast.literal_eval(assignment.value)
    }


def build_popular_movies():
    if not SOURCE_PATH.is_file():
        raise FileNotFoundError(f"Missing local IMDb catalog: {SOURCE_PATH}")

    excluded_titles = core_titles()
    source_movies, source_events = clean_catalog(
        json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    )
    write_catalog_audit_log(
        source_events,
        DATA_DIR / "popular_build_audit.jsonl",
    )
    candidates = [
        movie for movie in source_movies
        if movie.get("title")
        and movie.get("release_year")
        and movie.get("genres")
        and movie.get("vote_count", 0) >= MINIMUM_VOTES
        and (
            normalized_title(movie["title"]),
            movie["release_year"],
            movie.get("runtime"),
        ) not in excluded_titles
    ]
    candidates.sort(key=lambda movie: (
        -(movie.get("vote_count") or 0),
        -(movie.get("average_rating") or 0),
        movie.get("title", "").casefold(),
        movie.get("imdb_id", ""),
    ))

    popular_movies = []
    seen_ids = set()
    candidate_by_title = {}
    for movie in source_movies:
        if not movie.get("title") or not movie.get("release_year") or not movie.get("genres"):
            continue
        key = (normalized_title(movie["title"]), movie["release_year"])
        existing = candidate_by_title.get(key)
        if existing is None or movie.get("vote_count", 0) > existing.get("vote_count", 0):
            candidate_by_title[key] = movie
    ordered_candidates = [
        candidate_by_title[key]
        for key in PINNED_TITLES
        if key in candidate_by_title
        and (
            normalized_title(candidate_by_title[key]["title"]),
            candidate_by_title[key]["release_year"],
            candidate_by_title[key].get("runtime"),
        ) not in excluded_titles
    ]
    ordered_candidates.extend(candidates)
    for source_movie in ordered_candidates:
        identity = str(source_movie.get("imdb_id") or source_movie.get("id") or "")
        if identity and identity in seen_ids:
            continue
        if identity:
            seen_ids.add(identity)
        movie = dict(source_movie)
        movie["id"] = 66 + len(popular_movies)
        genres = ", ".join(movie["genres"])
        rating = movie.get("average_rating")
        rating_text = f"IMDb rating {rating:.1f}/10" if rating is not None else "IMDb rating unavailable"
        movie["synopsis"] = (
            f"{rating_text} from {movie['vote_count']:,} votes; "
            f"released in {movie['release_year']}. Genres: {genres}."
        )
        movie["themes"] = []
        movie["mood_tags"] = []
        movie["keywords"] = [movie["title"], *movie["genres"]]
        popular_movies.append(movie)
        if len(popular_movies) == POPULAR_MOVIE_COUNT:
            break

    if len(popular_movies) != POPULAR_MOVIE_COUNT:
        raise ValueError(f"Expected {POPULAR_MOVIE_COUNT} movies, found {len(popular_movies)}")

    popular_movies, output_events = clean_catalog(popular_movies)
    write_catalog_audit_log(
        [*source_events, *output_events],
        DATA_DIR / "popular_build_audit.jsonl",
    )
    if len(popular_movies) != POPULAR_MOVIE_COUNT:
        raise ValueError(f"Cleaning left {len(popular_movies)} popular movies")
    for index, movie in enumerate(popular_movies, start=66):
        movie["id"] = index
    annotate_awards(popular_movies)
    OUTPUT_PATH.write_text(json.dumps(popular_movies, ensure_ascii=False), encoding="utf-8")
    return popular_movies


if __name__ == "__main__":
    movies = build_popular_movies()
    print(
        f"Wrote {len(movies):,} popular movies; "
        f"minimum IMDb vote count: {min(movie['vote_count'] for movie in movies):,}; "
        f"award-labelled movies: {sum(bool(movie['notable_awards']) for movie in movies)}"
    )