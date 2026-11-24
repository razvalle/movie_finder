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

import re
import unicodedata
from functools import lru_cache

from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)
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
    "evidence": 0.68,
    "search": 0.10,
}

MIN_DESCRIPTION_SIMILARITY = 0.15


def _contains_phrase(text, phrase):
    text_tokens = _phrase_tokens(text)
    phrase_tokens = _phrase_tokens(phrase)
    if not phrase_tokens:
        return False
    for start in range(len(text_tokens) - len(phrase_tokens) + 1):
        if all(
            text_tokens[start + offset] in _word_forms(word)
            or word in _word_forms(text_tokens[start + offset])
            for offset, word in enumerate(phrase_tokens)
        ):
            return True
    return False


@lru_cache(maxsize=32768)
def _phrase_tokens(text):
    normalized = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", str(text)).casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


@lru_cache(maxsize=4096)
def _word_forms(word):
    forms = {word}
    if len(word) > 4 and word.endswith("ies"):
        forms.add(word[:-3] + "y")
    elif len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        forms.add(word[:-2])
    elif len(word) > 3 and word.endswith("s"):
        forms.add(word[:-1])
    return forms


def _movie_text_fields(movie):
    fields = [
        movie.get("title", ""), movie.get("original_title", ""),
        movie.get("synopsis", ""), movie.get("overview", ""),
        movie.get("tagline", ""), movie.get("certification", ""),
        movie.get("content_rating", ""), movie.get("rating_certificate", ""),
        movie.get("nationality", ""), movie.get("quality", ""),
    ]
    for key in (
        "genres", "themes", "mood_tags", "keywords", "content_descriptors",
        "production_countries", "origin_regions", "notable_awards", "cast", "actors",
    ):
        value = movie.get(key)
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    fields.append(item)
                elif isinstance(item, dict):
                    fields.extend(
                        str(field_value) for field_value in item.values()
                        if isinstance(field_value, str)
                    )
    return [str(value) for value in fields if value]


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
    """True if the movie matches any explicitly excluded catalog attribute."""
    searchable_fields = _movie_text_fields(movie)
    exclusions = (
        (preferences.get("excluded_genres", []), GENRE_SYNONYMS),
        (preferences.get("excluded_moods", []), MOOD_SYNONYMS),
        (preferences.get("excluded_themes", []), THEME_SYNONYMS),
        (preferences.get("excluded_content_descriptors", []), CONTENT_DESCRIPTOR_SYNONYMS),
    )
    for tags, synonym_table in exclusions:
        if any(
            _contains_phrase(field, form)
            for field in searchable_fields
            for tag in tags
            for form in {tag, *synonym_table.get(tag, [])}
        ):
            return True
    if any(
        _contains_phrase(field, term)
        for field in searchable_fields
        for term in preferences.get("excluded_keywords", [])
    ):
        return True
    return False


def score_movie(
    movie, preferences, tfidf_model, query_vector=None, query_norm=None,
    query_evidence=None, relevance_score=None,
):
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
    has_query_evidence = bool(
        query_evidence
        and (query_evidence.get("details") or query_evidence.get("associated_details"))
    )

    keyword_score = None
    if preferences["free_text_keywords"]:
        query_vec = query_vector or vectorize_query(preferences["free_text_keywords"], tfidf_model["idf"])
        movie_vec = tfidf_model["vectors"].get(movie["id"], {})
        movie_norm = tfidf_model.get("norms", {}).get(movie["id"])
        keyword_score = cosine_similarity(query_vec, movie_vec, query_norm, movie_norm)
        if has_query_evidence:
            keyword_score *= query_evidence.get("strength", 0.0)

    evidence_score = None
    search_score = None
    if has_query_evidence:
        evidence_score = query_evidence.get("score", 0.0)
    if relevance_score is not None and relevance_score > 0:
        search_score = relevance_score / (1 + relevance_score)
        if has_query_evidence:
            search_score *= query_evidence.get("strength", 0.0)

    factors = {
        "genre": genre_result["fraction"] if genre_result else None,
        "mood": mood_result["fraction"] if mood_result else None,
        "theme": theme_result["fraction"] if theme_result else None,
        "keyword": keyword_score,
        "runtime": runtime_score,
        "release_year": year_score,
        "evidence": evidence_score,
        "search": search_score,
    }

    active_factors = {k: v for k, v in factors.items() if v is not None}
    weight_sum = sum(BASE_WEIGHTS[k] for k in active_factors)

    if not active_factors:
        percent = 0
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
            "evidence": evidence_score,
            "search": search_score,
        },
    }


def rank_movies(
    movies, preferences, tfidf_model, evidence_by_id=None,
    relevance_by_id=None, apply_filters=True,
):
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
        if apply_filters and preferences["genres"] and not genre_matches:
            excluded_count += 1
            continue
        available_mood_match = mood_matches if mood_metadata_available else False
        available_theme_match = theme_matches if theme_metadata_available else False
        has_available_descriptive_filter = (
            (preferences["moods"] and mood_metadata_available)
            or (preferences["themes"] and theme_metadata_available)
        )
        if apply_filters and not preferences["genres"] and has_available_descriptive_filter:
            if not (available_mood_match or available_theme_match):
                excluded_count += 1
                continue
        if apply_filters and is_excluded(movie, preferences):
            excluded_count += 1
            continue
        movie_id = movie["id"]
        outcome = score_movie(
            movie, preferences, tfidf_model, query_vector, query_norm,
            (evidence_by_id or {}).get(str(movie_id), (evidence_by_id or {}).get(movie_id)),
            (relevance_by_id or {}).get(str(movie_id), (relevance_by_id or {}).get(movie_id)),
        )

        # Unknown-only text must not fall back to showing the whole catalog.
        # When a structured preference exists, unknown words are harmless
        # noise and the structured filter remains authoritative.
        has_structured_preference = any((
            preferences["genres"], preferences["moods"], preferences["themes"],
            preferences["runtime"]["min"], preferences["runtime"]["max"],
            preferences["runtime"]["target"], preferences["release_year"],
        ))
        if apply_filters and preferences["free_text_keywords"] and not (
            preferences["genres"] or preferences["moods"] or preferences["themes"]
        ):
            keyword_score = outcome["breakdown"]["keyword"]
            evidence_score = outcome["breakdown"]["evidence"] or 0
            if max(keyword_score or 0, evidence_score) < MIN_DESCRIPTION_SIMILARITY:
                excluded_count += 1
                continue
        scored.append({"movie": movie, "percent": outcome["percent"], "breakdown": outcome["breakdown"]})

    scored.sort(key=lambda r: r["percent"], reverse=True)
    return {"results": scored, "excluded_count": excluded_count}
