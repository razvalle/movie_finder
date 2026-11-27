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
from .match_policy import BASE_WEIGHTS

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


def _movie_tag_text_fields(movie):
    fields = [
        movie.get("synopsis", ""), movie.get("overview", ""),
    ]
    for key in (
        "genres", "mood_tags", "themes", "keywords", "content_descriptors",
    ):
        value = movie.get(key)
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, list):
            fields.extend(item for item in value if isinstance(item, str))
    return [value for value in fields if value]


_GLOBAL_SYNONYM_TABLES = (
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
    CONTENT_DESCRIPTOR_SYNONYMS,
)


def _build_concept_forms(tag, tables):
    forms = {tag}
    for table in tables:
        if not table:
            continue
        for canonical, aliases in table.items():
            if tag == canonical or tag in aliases:
                forms.update((canonical, *aliases))
    related_forms = set(forms)
    for table in tables[1:]:
        for canonical, aliases in table.items():
            group = {canonical, *aliases}
            if related_forms.intersection(group):
                forms.update(group)
    return forms


@lru_cache(maxsize=4096)
def _cached_global_concept_forms(tag):
    return frozenset(_build_concept_forms(tag, _GLOBAL_SYNONYM_TABLES))


def _concept_forms(tag, preferred_table=None):
    if preferred_table is None or any(
        preferred_table is table for table in _GLOBAL_SYNONYM_TABLES
    ):
        return set(_cached_global_concept_forms(tag))
    return _build_concept_forms(
        tag, (preferred_table, *_GLOBAL_SYNONYM_TABLES),
    )


def _movie_contains_concept(movie, tag, preferred_table=None):
    return any(
        _contains_phrase(field, form)
        for field in _movie_tag_text_fields(movie)
        for form in _concept_forms(tag, preferred_table)
    )


def _fields_contain_concept(fields, tag, preferred_table):
    return any(
        _contains_phrase(field, form)
        for field in fields
        for form in _concept_forms(tag, preferred_table)
    )


def _concept_overlap_fraction(requested, movie, preferred_table=None):
    if not requested:
        return None
    matched = [
        tag for tag in requested
        if _movie_contains_concept(movie, tag, preferred_table)
    ]
    missing = [tag for tag in requested if tag not in matched]
    return {
        "fraction": len(matched) / len(requested),
        "matched": matched,
        "missing": missing,
    }


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
    if movie_runtime is None:
        return None
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
    if movie_year is None:
        return None
    min_, max_ = constraint["min"], constraint["max"]
    if min_ is not None and movie_year < min_:
        return max(0.0, 1 - (min_ - movie_year) / 15)
    if max_ is not None and movie_year > max_:
        return max(0.0, 1 - (movie_year - max_) / 15)
    return 1.0


def evaluate_runtime_status(movie, constraint):
    constraint = constraint or {}
    low, high, target = (
        constraint.get("min"), constraint.get("max"), constraint.get("target"),
    )
    if low is None and high is None and target is None:
        return "satisfied"
    runtime = movie.get("runtime")
    if isinstance(runtime, bool) or not isinstance(runtime, (int, float)) or runtime <= 0:
        return "unverifiable"
    if low is not None and runtime < low:
        return "violated"
    if high is not None and runtime > high:
        return "violated"
    return "satisfied"


def is_excluded(movie, preferences):
    """True if the movie matches any explicitly excluded catalog attribute."""
    exclusions = (
        (preferences.get("excluded_genres", []), GENRE_SYNONYMS),
        (preferences.get("excluded_moods", []), MOOD_SYNONYMS),
        (preferences.get("excluded_themes", []), THEME_SYNONYMS),
        (preferences.get("excluded_content_descriptors", []), CONTENT_DESCRIPTOR_SYNONYMS),
    )
    searchable_fields = _movie_text_fields(movie)
    for tags, synonym_table in exclusions:
        if any(
            _fields_contain_concept(searchable_fields, tag, synonym_table)
            for tag in tags
        ):
            return True
    if any(
        _contains_phrase(field, term)
        for field in searchable_fields
        for term in preferences.get("excluded_keywords", [])
    ):
        return True
    return False


def evaluate_movie_constraints(movie, preferences, active_genres=None):
    preferences = preferences or {}
    checks = []
    genres = list(dict.fromkeys([
        *(preferences.get("genres") or []),
        *(active_genres or []),
    ]))
    if genres:
        actual_genres = movie.get("genres") or []
        status = (
            "satisfied" if any(
                _movie_contains_concept(movie, tag, GENRE_SYNONYMS)
                for tag in genres
            )
            else "unverifiable" if not actual_genres
            else "violated"
        )
        checks.append({"constraint": "genre", "status": status})

    runtime = preferences.get("runtime") or {}
    runtime_status = evaluate_runtime_status(movie, runtime)
    if any(runtime.get(key) is not None for key in ("min", "max", "target")):
        checks.append({"constraint": "runtime", "status": runtime_status})

    year = preferences.get("release_year") or {}
    if year and any(year.get(key) is not None for key in ("min", "max")):
        movie_year = movie.get("release_year")
        if not isinstance(movie_year, int) or isinstance(movie_year, bool) or movie_year <= 0:
            year_status = "unverifiable"
        elif (
            year.get("min") is not None and movie_year < year["min"]
        ) or (
            year.get("max") is not None and movie_year > year["max"]
        ):
            year_status = "violated"
        else:
            year_status = "satisfied"
        checks.append({"constraint": "release_year", "status": year_status})

    if not genres and (preferences.get("moods") or preferences.get("themes")):
        moods = preferences.get("moods") or []
        themes = preferences.get("themes") or []
        mood_tags = movie.get("mood_tags") or []
        theme_tags = movie.get("themes") or []
        if any(
            _movie_contains_concept(movie, tag, MOOD_SYNONYMS)
            for tag in moods
        ) or any(
            _movie_contains_concept(movie, tag, THEME_SYNONYMS)
            for tag in themes
        ):
            descriptive_status = "satisfied"
        elif (
            (not moods or mood_tags)
            and (not themes or theme_tags)
        ):
            descriptive_status = "violated"
        else:
            descriptive_status = "unverifiable"
        checks.append({
            "constraint": "mood_or_theme",
            "status": descriptive_status,
        })

    exclusion_fields = {
        "excluded_genres": ("genres", "genre"),
        "excluded_moods": ("mood_tags", "mood"),
        "excluded_themes": ("themes", "theme"),
        "excluded_content_descriptors": ("content_descriptors", "content descriptor"),
        "excluded_keywords": ("keywords", "keyword"),
    }
    active_exclusion_fields = [
        (key, field, label)
        for key, (field, label) in exclusion_fields.items()
        if preferences.get(key)
    ]
    if active_exclusion_fields:
        if is_excluded(movie, preferences):
            exclusion_status = "violated"
        elif any(not movie.get(field) for _, field, _ in active_exclusion_fields):
            exclusion_status = "unverifiable"
        else:
            exclusion_status = "satisfied"
        checks.append({
            "constraint": "exclusions",
            "status": exclusion_status,
        })

    statuses = {check["status"] for check in checks}
    overall = (
        "violated" if "violated" in statuses
        else "unverifiable" if "unverifiable" in statuses
        else "satisfied"
    )
    return {"status": overall, "checks": checks}


def score_movie(
    movie, preferences, tfidf_model, query_vector=None, query_norm=None,
    query_evidence=None, relevance_score=None, constraint_result=None,
):
    """
    Scores a single movie against extracted preferences.
    Returns {"percent": int, "breakdown": {...}} where breakdown holds
    each factor's raw sub-score and matched/missing details, used by
    explain.py.
    """
    genre_result = _concept_overlap_fraction(
        preferences["genres"], movie, GENRE_SYNONYMS,
    )
    mood_result = _concept_overlap_fraction(
        preferences["moods"], movie, MOOD_SYNONYMS,
    )
    theme_result = _concept_overlap_fraction(
        preferences["themes"], movie, THEME_SYNONYMS,
    )
    runtime_score = score_runtime(
        preferences["runtime"], movie.get("runtime"),
    )
    year_score = score_release_year(
        preferences["release_year"], movie.get("release_year"),
    )
    exclusion_score = None
    has_exclusions = any(
        preferences.get(key)
        for key in (
            "excluded_genres", "excluded_moods", "excluded_themes",
            "excluded_content_descriptors", "excluded_keywords",
        )
    )
    if has_exclusions:
        constraint_result = constraint_result or evaluate_movie_constraints(
            movie, preferences,
        )
        exclusion_status = next(
            (
                check["status"] for check in constraint_result["checks"]
                if check["constraint"] == "exclusions"
            ),
            None,
        )
        exclusion_score = {
            "satisfied": 1.0,
            "violated": 0.0,
        }.get(exclusion_status)
    has_content_terms = bool(
        preferences.get("free_text_keywords")
        or preferences.get("ambiguous_interpretations")
    )
    has_query_evidence = bool(
        has_content_terms and query_evidence
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
    if has_content_terms and has_query_evidence:
        evidence_score = query_evidence.get("score", 0.0)
    if has_content_terms and relevance_score is not None and relevance_score > 0:
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
        "exclusions": exclusion_score,
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

    soft_exclusions = preferences.get("soft_exclusions", [])
    for exclusion in soft_exclusions:
        category, _, tag = exclusion.partition(":")
        primary_tags = {
            "genre": movie.get("genres", []),
            "mood": movie.get("mood_tags", []),
            "theme": movie.get("themes", []),
            "content_descriptor": movie.get("content_descriptors", []),
        }.get(category, [])
        if tag in primary_tags:
            percent -= 15

    return {
        "percent": max(0, min(100, percent)),
        "breakdown": {
            "genre": genre_result,
            "mood": mood_result,
            "theme": theme_result,
            "keyword": keyword_score,
            "runtime": runtime_score,
            "release_year": year_score,
            "exclusions": exclusion_score,
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
    query_vector = vectorize_query(preferences["free_text_keywords"], tfidf_model["idf"])
    query_norm = vector_norm(query_vector) if query_vector else 0.0

    for movie in movies:
        # An explicitly requested genre is a hard requirement. This keeps
        # broad requests such as "recommend horror" focused on that genre.
        constraint_result = evaluate_movie_constraints(movie, preferences)
        if apply_filters and constraint_result["status"] == "violated":
            excluded_count += 1
            continue
        movie_id = movie["id"]
        outcome = score_movie(
            movie, preferences, tfidf_model, query_vector, query_norm,
            (evidence_by_id or {}).get(str(movie_id), (evidence_by_id or {}).get(movie_id)),
            (relevance_by_id or {}).get(str(movie_id), (relevance_by_id or {}).get(movie_id)),
            constraint_result,
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
        scored.append({
            "movie": movie,
            "percent": outcome["percent"],
            "breakdown": outcome["breakdown"],
            "constraint_status": constraint_result["status"],
            "constraint_checks": constraint_result["checks"],
            "unverified": constraint_result["status"] == "unverifiable",
        })

    scored.sort(key=lambda result: (
        result["unverified"], -result["percent"],
    ))
    return {
        "results": scored,
        "excluded_count": excluded_count,
        "unverified_count": sum(result["unverified"] for result in scored),
    }
