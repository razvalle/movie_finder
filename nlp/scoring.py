"""
nlp/scoring.py
-----------------------------------------------------------------------
STEPS 13-14 of the pipeline: WEIGHTED SCORING + RANKING

Two-phase approach:
  1. HARD FILTER -- movies matching an excluded genre/mood/theme are
     removed entirely (spec: "excluded preferences should either remove
     the movie or apply a strong penalty" -- we remove, since showing a
     horror movie to someone who said "not horror" is never useful, and
     the excluded-count is surfaced in the UI so the exclusion stays
     explainable).
  2. WEIGHTED SCORE -- every remaining movie is scored 0-100 against
     only the preference categories the user actually specified.
     Unmentioned categories are dropped from the weight pool and the
     remaining weights are re-normalized to sum to 1, so the score is
     always "how well does this match what you asked for", not "how
     many boxes does it tick overall".
-----------------------------------------------------------------------
"""

from .similarity import cosine_similarity, vector_norm
from .tfidf import vectorize_query

# Base weights when ALL categories are present in the query. Re-normalized
# per-query based on which categories were actually detected.
BASE_WEIGHTS = {
    "genre": 0.28,
    "mood": 0.24,
    "theme": 0.14,
    "keyword": 0.16,  # TF-IDF/cosine similarity against free-text description
    "runtime": 0.12,
    "release_year": 0.06,
}

MIN_DESCRIPTION_SIMILARITY = 0.15


def overlap_fraction(requested, actual):
    """Returns None if `requested` is empty (category not requested), else {"fraction", "matched", "missing"}."""
    if not requested:
        return None
    actual_set = set(actual)
    matched = [r for r in requested if r in actual_set]
    missing = [r for r in requested if r not in actual_set]
    return {"fraction": len(matched) / len(requested), "matched": matched, "missing": missing}


def score_runtime(constraint, movie_runtime):
    min_, max_, target, tolerance = constraint["min"], constraint["max"], constraint["target"], constraint["tolerance"]
    if min_ is None and max_ is None and target is None:
        return None  # not requested

    if target is not None:
        diff = abs(movie_runtime - target)
        return max(0.0, 1 - diff / (tolerance * 2))
    if max_ is not None and min_ is not None:
        if min_ <= movie_runtime <= max_:
            return 1.0
        over = movie_runtime - max_ if movie_runtime > max_ else min_ - movie_runtime
        return max(0.0, 1 - over / 30)
    if max_ is not None:
        if movie_runtime <= max_:
            return 1.0
        return max(0.0, 1 - (movie_runtime - max_) / 30)
    if min_ is not None:
        if movie_runtime >= min_:
            return 1.0
        return max(0.0, 1 - (min_ - movie_runtime) / 30)
    return None


def score_release_year(constraint, movie_year):
    if not constraint:
        return None
    min_, max_ = constraint["min"], constraint["max"]
    if min_ is not None and movie_year < min_:
        return max(0.0, 1 - (min_ - movie_year) / 15)
    if max_ is not None and movie_year > max_:
        return max(0.0, 1 - (movie_year - max_) / 15)
    return 1.0


def is_excluded(movie, preferences):
    """True if the movie should be hard-filtered out due to an excluded genre/mood/theme match."""
    if any(g in movie["genres"] for g in preferences["excluded_genres"]):
        return True
    if any(m in movie["mood_tags"] for m in preferences["excluded_moods"]):
        return True
    if any(t in movie["themes"] for t in preferences["excluded_themes"]):
        return True
    return False


def score_movie(movie, preferences, tfidf_model, query_vector=None, query_norm=None):
    """
    Scores a single movie against extracted preferences.
    Returns {"percent": int, "breakdown": {...}} where breakdown holds
    each factor's raw sub-score and matched/missing details, used by
    explain.py.
    """
    genre_result = overlap_fraction(preferences["genres"], movie["genres"])
    mood_result = overlap_fraction(preferences["moods"], movie["mood_tags"])
    theme_result = overlap_fraction(preferences["themes"], movie["themes"])
    runtime_score = score_runtime(preferences["runtime"], movie["runtime"])
    year_score = score_release_year(preferences["release_year"], movie["release_year"])

    keyword_score = None
    if preferences["free_text_keywords"]:
        query_vec = query_vector or vectorize_query(preferences["free_text_keywords"], tfidf_model["idf"])
        movie_vec = tfidf_model["vectors"].get(movie["id"], {})
        movie_norm = tfidf_model.get("norms", {}).get(movie["id"])
        keyword_score = cosine_similarity(query_vec, movie_vec, query_norm, movie_norm)

    factors = {
        "genre": genre_result["fraction"] if genre_result else None,
        "mood": mood_result["fraction"] if mood_result else None,
        "theme": theme_result["fraction"] if theme_result else None,
        "keyword": keyword_score,
        "runtime": runtime_score,
        "release_year": year_score,
    }

    active_factors = {k: v for k, v in factors.items() if v is not None}
    weight_sum = sum(BASE_WEIGHTS[k] for k in active_factors)

    if not active_factors:
        # Free-text query didn't match any structured category or
        # keyword -- fall back to a small baseline so results still
        # render, ranked by whatever weak similarity exists.
        percent = 20
    else:
        weighted = sum(v * (BASE_WEIGHTS[k] / weight_sum) for k, v in active_factors.items())
        percent = round(weighted * 100)

    return {
        "percent": max(0, min(100, percent)),
        "breakdown": {
            "genre": genre_result,
            "mood": mood_result,
            "theme": theme_result,
            "keyword": keyword_score,
            "runtime": runtime_score,
            "release_year": year_score,
        },
    }


def rank_movies(movies, preferences, tfidf_model):
    """
    Filters, scores, and ranks the full dataset for a preference dict.
    Returns {"results": [...], "excluded_count": int} where results is
    sorted descending by percent and each entry is
    {"movie", "percent", "breakdown"}.
    """
    excluded_count = 0
    scored = []
    mood_metadata_available = any(movie.get("mood_tags") for movie in movies)
    theme_metadata_available = any(movie.get("themes") for movie in movies)
    query_vector = vectorize_query(preferences["free_text_keywords"], tfidf_model["idf"])
    query_norm = vector_norm(query_vector) if query_vector else 0.0

    for movie in movies:
        # An explicitly requested genre is a hard requirement. This keeps
        # broad requests such as "recommend horror" focused on that genre.
        genre_matches = any(value in movie["genres"] for value in preferences["genres"])
        mood_matches = any(value in movie["mood_tags"] for value in preferences["moods"])
        theme_matches = any(value in movie["themes"] for value in preferences["themes"])

        # A named genre is an explicit catalog filter. Descriptive moods and
        # themes are alternatives: "grieving family dealing with loss" can
        # match either the sad mood or the grief theme without over-filtering.
        if preferences["genres"] and not genre_matches:
            excluded_count += 1
            continue
        available_mood_match = mood_matches if mood_metadata_available else False
        available_theme_match = theme_matches if theme_metadata_available else False
        has_available_descriptive_filter = (
            (preferences["moods"] and mood_metadata_available)
            or (preferences["themes"] and theme_metadata_available)
        )
        if not preferences["genres"] and has_available_descriptive_filter:
            if not (available_mood_match or available_theme_match):
                excluded_count += 1
                continue
        if is_excluded(movie, preferences):
            excluded_count += 1
            continue
        outcome = score_movie(movie, preferences, tfidf_model, query_vector, query_norm)

        # Unknown-only text must not fall back to showing the whole catalog.
        # When a structured preference exists, unknown words are harmless
        # noise and the structured filter remains authoritative.
        has_structured_preference = any((
            preferences["genres"], preferences["moods"], preferences["themes"],
            preferences["runtime"]["min"], preferences["runtime"]["max"],
            preferences["runtime"]["target"], preferences["release_year"],
        ))
        if preferences["free_text_keywords"] and not (
            preferences["genres"] or preferences["moods"] or preferences["themes"]
        ):
            keyword_score = outcome["breakdown"]["keyword"]
            if keyword_score is None or keyword_score < MIN_DESCRIPTION_SIMILARITY:
                excluded_count += 1
                continue
        scored.append({"movie": movie, "percent": outcome["percent"], "breakdown": outcome["breakdown"]})

    scored.sort(key=lambda r: r["percent"], reverse=True)
    return {"results": scored, "excluded_count": excluded_count}
