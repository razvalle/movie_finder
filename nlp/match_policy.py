"""Shared result weights and thresholds for the match pipeline."""

from data.movies import MOVIES
from .catalog_matching import load_query_rules

BASE_WEIGHTS = {
    "genre": 0.28,
    "mood": 0.24,
    "theme": 0.14,
    "keyword": 0.16,
    "runtime": 0.12,
    "release_year": 0.06,
    "exclusions": 0.18,
    "evidence": 0.68,
    "search": 0.10,
}

MATCH_THRESHOLDS = {
    "exact": 80,
    "close": 50,
}

_TOTAL_VOTES = sum(movie.get("vote_count") or 0 for movie in MOVIES)
RATING_PRIOR = sum(
    (movie.get("average_rating") or 0) * (movie.get("vote_count") or 0)
    for movie in MOVIES
) / max(1, _TOTAL_VOTES)
RATING_MINIMUM_VOTES = int(load_query_rules()["bayesian_minimum_votes"])
