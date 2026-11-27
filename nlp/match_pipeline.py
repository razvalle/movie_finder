"""Shared result evidence, score, tier, reason, and ordering decisions."""

import logging
import math
import re

from .explain import title_case
from .scoring import (
    _contains_phrase,
    _movie_contains_concept,
    evaluate_runtime_status,
    is_excluded,
    score_runtime,
)
from .match_policy import MATCH_THRESHOLDS, RATING_MINIMUM_VOTES, RATING_PRIOR
from data.synonyms import GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS

_LOGGER = logging.getLogger(__name__)
_TIER_ORDER = {"exact": 3, "close": 2, "related": 1}
_DETAIL_FIELDS = {
    "title": ("title", "original_title"),
    "synopsis": ("synopsis", "overview", "tagline"),
    "overview": ("overview", "synopsis"),
    "keyword": ("keywords",),
    "theme": ("themes",),
    "genre": ("genres",),
    "mood": ("mood_tags",),
}


def _values(movie, field):
    value = movie.get(field)
    if isinstance(value, str):
        return [value]
    return value if isinstance(value, list) else []


def _bayesian_rating(movie):
    rating = movie.get("average_rating")
    votes = movie.get("vote_count")
    rating = float(rating) if isinstance(rating, (int, float)) else 0.0
    votes = int(votes) if isinstance(votes, (int, float)) and votes > 0 else 0
    total = votes + RATING_MINIMUM_VOTES
    return (
        votes * rating + RATING_MINIMUM_VOTES * RATING_PRIOR
    ) / total if total else RATING_PRIOR


def _evidence(movie, candidate):
    query_evidence = candidate.get("query_evidence") or {}
    details = []
    for detail in query_evidence.get("details", []):
        phrase = str(detail.get("phrase") or "").strip()
        source = str(detail.get("source") or "").casefold()
        fields = _DETAIL_FIELDS.get(source, ())
        if phrase and any(
            _contains_phrase(value, phrase)
            for field in fields
            for value in _values(movie, field)
            if isinstance(value, str)
        ):
            details.append({**detail, "phrase": phrase, "source": source})
    associated = []
    for detail in query_evidence.get("associated_details", []):
        phrase = str(detail.get("phrase") or "").strip()
        concept = str(detail.get("concept") or "").strip()
        source = str(detail.get("source") or "").casefold()
        fields = _DETAIL_FIELDS.get(source, ())
        if phrase and concept and any(
            _contains_phrase(value, phrase)
            for field in fields
            for value in _values(movie, field)
            if isinstance(value, str)
        ):
            associated.append({**detail, "phrase": phrase, "concept": concept, "source": source})
    return details, associated


def _structured_matches(movie, preferences, selected_genres=()):
    matched = []
    genres = list(dict.fromkeys([
        *preferences.get("genres", []),
        *selected_genres,
    ]))
    checks = (
        ("moods", MOOD_SYNONYMS, "mood"),
        ("themes", THEME_SYNONYMS, "theme"),
    )
    for value in genres:
        if _movie_contains_concept(movie, value, GENRE_SYNONYMS):
            matched.append(("genre", value))
    for key, synonyms, label in checks:
        for value in preferences.get(key, []):
            if _movie_contains_concept(movie, value, synonyms):
                matched.append((label, value))
    runtime = preferences.get("runtime") or {}
    runtime_value = movie.get("runtime")
    if any(runtime.get(key) is not None for key in ("min", "max", "target")):
        if runtime.get("target") is not None:
            runtime_score = score_runtime(runtime, runtime_value)
            if runtime_score is not None and runtime_score >= 0.6:
                matched.append(("runtime", runtime_value))
        elif evaluate_runtime_status(movie, runtime) == "satisfied":
            matched.append(("runtime", runtime_value))
    year = preferences.get("release_year") or {}
    year_value = movie.get("release_year")
    if any(year.get(key) is not None for key in ("min", "max")):
        if isinstance(year_value, int) and not isinstance(year_value, bool):
            if (
                (year.get("min") is None or year_value >= year["min"])
                and (year.get("max") is None or year_value <= year["max"])
            ):
                matched.append(("year", year_value))
    return matched


def _positive_constraint_count(preferences, selected_genres=()):
    runtime = preferences.get("runtime") or {}
    year = preferences.get("release_year") or {}
    return sum((
        len(set(preferences.get("genres", [])) | set(selected_genres)),
        len(preferences.get("moods", [])),
        len(preferences.get("themes", [])),
        int(any(runtime.get(key) is not None for key in ("min", "max", "target"))),
        int(any(year.get(key) is not None for key in ("min", "max"))),
    ))


def _validated_reason(movie, text):
    text = " ".join(str(text or "").split())
    invalid = (
        not text
        or "associated with \"\"" in text.casefold()
        or "()" in text
        or re.search(r"\b(?:undefined|null|none)\b", text, re.IGNORECASE)
        or re.search(r"\s{2,}", text)
    )
    if invalid:
        _LOGGER.warning("Rejected invalid match reason for movie id=%s: %r", movie.get("id"), text)
        return False
    return True


def _build_reason(
    movie, preferences, candidate, direct, associated, exact_title, series,
    selected_genres=(),
):
    similarity_source = str(candidate.get("similarity_source_title") or "").strip()
    if similarity_source:
        reason = f"Ranked by metadata similarity to {similarity_source}."
        return (
            reason if _validated_reason(movie, reason)
            else "Ranked by catalog metadata similarity.",
            [{"ok": True, "text": reason.rstrip(".")}],
        )
    if exact_title:
        reason = f"Matched title: {movie.get('title', '').strip()}."
        return (
            reason if _validated_reason(movie, reason) else "Matched an exact catalog title.",
            [{"ok": True, "text": reason.rstrip(".")}],
        )
    if series:
        reason = f"Same series as {series}."
        return (
            reason if _validated_reason(movie, reason) else "Matched a catalog series.",
            [{"ok": True, "text": reason.rstrip(".")}],
        )

    reasons = []
    reason_items = []
    for label, value in _structured_matches(movie, preferences, selected_genres):
        if label == "runtime":
            runtime = preferences.get("runtime") or {}
            lower, upper, target = (
                runtime.get("min"), runtime.get("max"), runtime.get("target"),
            )
            if target is not None:
                phrase = f"Runtime is {value:g} minutes (near {target} minutes)"
            elif lower is not None and upper is not None:
                phrase = f"Runtime is {value:g} minutes (within {lower}-{upper} minutes)"
            elif upper is not None:
                phrase = f"Runtime is {value:g} minutes (under {upper})"
            elif lower is not None:
                phrase = f"Runtime is {value:g} minutes (at least {lower})"
            else:
                phrase = f"Runtime is {value:g} minutes"
            reasons.append(phrase)
        elif label == "year":
            phrase = f"Released in {value}"
            reasons.append(phrase)
        else:
            phrase = f"{title_case(value)} {label} matched"
            reasons.append(phrase)
        reason_items.append({"ok": True, "text": phrase})
    for value in selected_genres:
        if not _movie_contains_concept(movie, value, GENRE_SYNONYMS):
            phrase = f"Missing: {title_case(value)} genre"
            reasons.append(phrase)
            reason_items.append({"ok": False, "text": phrase})
    for value in preferences.get("genres", []):
        if not _movie_contains_concept(movie, value, GENRE_SYNONYMS):
            phrase = f"Missing: {title_case(value)} genre"
            if phrase not in reasons:
                reasons.append(phrase)
                reason_items.append({"ok": False, "text": phrase})

    for detail in direct:
        source = {
            "title": "title",
            "synopsis": "synopsis",
            "overview": "synopsis",
            "keyword": "keywords",
            "theme": "themes",
            "genre": "genres",
            "mood": "mood tags",
        }.get(detail["source"], "movie metadata")
        statement = f'"{detail["phrase"]}" in {source}'
        if statement not in reasons:
            reasons.append(statement)
            reason_items.append({"ok": True, "text": statement})
    for detail in associated:
        source = {
            "synopsis": "synopsis",
            "overview": "synopsis",
            "keyword": "keywords",
            "theme": "themes",
            "genre": "genres",
            "mood": "mood tags",
        }.get(detail["source"], "movie metadata")
        reasons.append(
            f'"{detail["phrase"]}" in {source} (related to "{detail["concept"]}")'
        )
        reason_items.append({"ok": True, "text": reasons[-1]})

    for value in preferences.get("excluded_genres", []):
        if not is_excluded(movie, {"excluded_genres": [value]}):
            phrase = f"No {value} tag"
            reasons.append(phrase)
            reason_items.append({"ok": True, "text": phrase})
    for value in preferences.get("excluded_moods", []):
        if not is_excluded(movie, {"excluded_moods": [value]}):
            phrase = f"No {value} mood tag"
            reasons.append(phrase)
            reason_items.append({"ok": True, "text": phrase})
    for value in preferences.get("excluded_themes", []):
        if not is_excluded(movie, {"excluded_themes": [value]}):
            phrase = f"No {value} theme"
            reasons.append(phrase)
            reason_items.append({"ok": True, "text": phrase})

    if not reasons:
        phrase = "No direct evidence in available catalog fields"
        reasons.append(phrase)
        reason_items.append({"ok": False, "text": phrase})
    reason = "; ".join(reasons[:4]) + "."
    if _validated_reason(movie, reason):
        return reason, reason_items[:4]
    _LOGGER.warning("Match reason failed evidence validation for movie id=%s", movie.get("id"))
    fallback = "No direct evidence in available catalog fields."
    return fallback, [{"ok": False, "text": fallback.rstrip(".")}]


def finalize_results(
    results,
    candidates_by_id,
    preferences,
    *,
    exact_title_ids=(),
    title_match_ids=(),
    series_ids=(),
    series_title="",
    title_prefix=False,
    selected_genres=(),
    ranking_intent="",
):
    """Assign the final score/tier/reason and shared deterministic ordering."""
    exact_ids = {str(value) for value in exact_title_ids}
    title_ids = {str(value) for value in title_match_ids}
    series_ids = {str(value) for value in series_ids}
    selected_genres = tuple(selected_genres)
    finalized = []
    for entry in results:
        movie = entry["movie"]
        movie_id = str(movie.get("id"))
        candidate = candidates_by_id.get(movie_id, {})
        direct, associated = _evidence(movie, candidate)
        structured = _structured_matches(movie, preferences, selected_genres)
        query_evidence = candidate.get("query_evidence") or {}
        raw_percent = entry.get("percent")
        if not isinstance(raw_percent, (int, float)):
            raw_percent = 0
        similarity_score = candidate.get("similarity_score")
        if (
            isinstance(similarity_score, (int, float))
            and not isinstance(similarity_score, bool)
            and math.isfinite(similarity_score)
        ):
            percent = min(round(similarity_score), MATCH_THRESHOLDS["exact"] - 1)
        elif movie_id in exact_ids or movie_id in series_ids:
            percent = 100
        elif raw_percent > 0:
            percent = round(raw_percent)
        elif direct or associated:
            percent = round(max(
                float(candidate.get("search_score") or 0),
                float(query_evidence.get("score") or 0) * 100,
                float(candidate.get("evidence_strength") or 0) * 100,
                1,
            ))
        elif structured and _positive_constraint_count(preferences, selected_genres):
            percent = round(
                len(structured)
                / _positive_constraint_count(preferences, selected_genres) * 100
            )
        elif preferences.get("excluded_genres") or preferences.get("excluded_moods") or preferences.get("excluded_themes"):
            percent = 100
        else:
            percent = 0
        percent = max(0, min(100, percent))

        positive_total = _positive_constraint_count(preferences, selected_genres)
        structured_complete = bool(positive_total and len(structured) >= positive_total)
        evidence_sources = {item["source"] for item in direct}
        direct_tier = bool(evidence_sources.intersection({
            "title", "synopsis", "overview", "keyword", "theme",
        })) or structured_complete
        associated_only = bool(associated) and not direct_tier
        broad_only = bool(direct) and evidence_sources <= {"genre", "mood"}
        eligible = (
            candidate.get("match_eligible", False)
            and not candidate.get("unsatisfied")
            and not candidate.get("unverifiable")
        )
        ambiguous = bool(preferences.get("ambiguous_interpretations"))
        exact = movie_id in exact_ids or movie_id in series_ids or (
            eligible and not ambiguous and percent >= MATCH_THRESHOLDS["exact"]
            and direct_tier
        )
        if exact:
            tier = "exact"
            level = "full"
        elif (
            candidate.get("fallback_only") and not direct and not associated and not structured
        ):
            tier = "close"
            level = "partial"
        elif associated_only or broad_only:
            tier = "related"
            level = "related"
            percent = min(percent, MATCH_THRESHOLDS["close"] - 1)
        else:
            tier = "close"
            level = "partial"
        if title_prefix and movie_id in title_ids:
            tier = "close"
            level = "partial"
            percent = min(percent, MATCH_THRESHOLDS["exact"] - 1)

        series = series_title if series_title and movie_id in series_ids else ""
        reason, reason_items = _build_reason(
            movie, preferences, candidate, direct, associated,
            movie_id in exact_ids and movie_id not in series_ids,
            series, selected_genres,
        )
        finalized.append({
            **entry,
            "percent": percent,
            "final_score": percent,
            "match_tier": tier,
            "match_level": level,
            "match_eligible": exact,
            "evidence_strength": max(
                float(candidate.get("evidence_strength") or 0),
                float(query_evidence.get("strength") or 0),
                float(candidate.get("search_score") or 0) / 100,
            ),
            "match_reason": reason,
            "reasons": reason_items,
            "genre_match_count": sum(
                1 for genre in selected_genres
                if _movie_contains_concept(movie, genre, GENRE_SYNONYMS)
            ),
            "search_score": (
                float(similarity_score)
                if isinstance(similarity_score, (int, float))
                and not isinstance(similarity_score, bool)
                and math.isfinite(similarity_score)
                else entry.get("search_score")
            ),
            "pipeline_evidence_tier": (
                "exact-title-or-series" if movie_id in exact_ids or movie_id in series_ids
                else "direct" if direct_tier
                else "specific-expansion" if associated
                else "broad-tag" if structured
                else "none"
            ),
        })

    def ordering(entry):
        movie = entry["movie"]
        genre_match_count = sum(
            1 for genre in selected_genres
            if _movie_contains_concept(movie, genre, GENRE_SYNONYMS)
        )
        relevance = entry.get("search_score") or entry.get("percent") or 0
        rating = _bayesian_rating(movie)
        votes = movie.get("vote_count") or 0
        if ranking_intent == "worst":
            rating = -rating
        elif ranking_intent == "popular":
            rating = float(votes)
        return (
            -_TIER_ORDER.get(entry["match_tier"], 0),
            -genre_match_count,
            -float(relevance),
            -rating,
            -int(votes),
            str(movie.get("title", "")).casefold(),
            str(movie.get("id", "")),
        )

    finalized.sort(key=ordering)
    return finalized


def tier_counts(results):
    counts = {"exact": 0, "close": 0, "related": 0}
    for entry in results:
        tier = entry.get("match_tier")
        if tier in counts:
            counts[tier] += 1
    return counts


def tier_count_label(results, selected_genres=()):
    counts = tier_counts(results)
    label = ", ".join(
        f"{counts[tier]} {tier}" for tier in ("exact", "close", "related")
    )
    unverified = sum(bool(entry.get("filter_unverified")) for entry in results)
    if unverified:
        label += f" — {unverified} unverified"
    if selected_genres:
        all_count = sum(
            all(
                _movie_contains_concept(entry["movie"], genre, GENRE_SYNONYMS)
                for genre in selected_genres
            )
            for entry in results
        )
        partial_count = len(results) - all_count
        label += f" — {all_count} match all selected genres, {partial_count} match some"
    return label


def order_browse_movies(movies, selected_genres=()):
    """Use the shared tier/evidence/rating tie-break order for browse results."""
    selected_genres = tuple(selected_genres)

    def key(movie):
        matched = sum(
            1 for genre in selected_genres
            if _movie_contains_concept(movie, genre, GENRE_SYNONYMS)
        )
        return (
            -matched,
            -_bayesian_rating(movie),
            str(movie.get("title", "")).casefold(),
            str(movie.get("id", "")),
        )

    return sorted(movies, key=key)
