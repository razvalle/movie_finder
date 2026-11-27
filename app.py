"""
app.py
-----------------------------------------------------------------------
APPLICATION ENTRY POINT (Flask)
-----------------------------------------------------------------------
The only file that touches HTTP/rendering. The whole page is a plain
GET form: the browser submits the query as ?q=... and this route
recomputes results server-side and re-renders the template. No
client-side JavaScript is required anywhere in this app -- every piece
of behavior (search, example chips) is implemented as ordinary links
and form submissions.

Run with:
    python app.py
or:
    flask --app app run --debug
-----------------------------------------------------------------------
"""

import copy
import re
import math
import heapq
import logging
import json
import time
import threading
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
import pycountry

from flask import (
    Flask, g, has_request_context, jsonify, make_response, redirect,
    render_template, request, url_for,
)

from data.catalog_version import catalog_version
from data.movies import MOVIES
from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)
from feedback_store import FEEDBACK_DB_PATH, record_feedback
from poster_storage import poster_public_base_url, poster_public_url
from nlp.extract import extract_preferences, remove_negated_terms
from nlp.normalize import normalize_text
from nlp.tokenize import STOPWORDS as TOKEN_STOPWORDS
from nlp.query import (
    build_structured_query,
    detect_cast_gender_constraint,
    detect_country,
    detect_people_and_language,
)
from nlp.tfidf import (
    load_or_build_tfidf_model,
    movie_to_similarity_document_text,
)
from nlp.scoring import (
    evaluate_movie_constraints,
    evaluate_runtime_status,
    rank_movies,
)
from nlp.explain import explain_match, title_case
from nlp.conversation import build_alternative_queries, build_assistant_message
from nlp.smart_search import (
    EXACT_MATCH_THRESHOLD,
    MAX_QUERY_LENGTH,
    MAX_CANDIDATES,
    STOP_WORDS,
    allow_search,
    interpret_query,
    retrieve_candidates,
    retrieve_plain_candidates,
    rerank_candidates,
)
from nlp.concepts import load_or_rebuild_concept_table, normalize_phrase
from nlp import smart_search as smart_search_module
from nlp.cast_verification import classify_cast_gender, evaluate_cast_constraint
from nlp.search_diagnostics import (
    SEARCH_BUDGET_SECONDS,
    SearchTrace,
    write_search_record,
)
from nlp.catalog_matching import (
    SERIES_MEMBER_MATCH_PERCENT,
    detect_title_spans,
    derive_series_groups,
    find_like_source_text,
    load_query_rules,
    normalize_title,
    title_aliases,
    title_risk_vocabulary,
    resolve_like_title,
)

TITLE_STOP_WORDS = set(STOP_WORDS) | TOKEN_STOPWORDS

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
CATALOG_VERSION = catalog_version(MOVIES)


@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; connect-src 'self'; "
        "font-src 'self' data: https:; form-action 'self'; frame-ancestors 'self'; "
        "img-src 'self' data: https:; object-src 'none'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https:"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


_TOTAL_VOTES = sum(movie.get("vote_count") or 0 for movie in MOVIES)
_BAYESIAN_RATING_PRIOR = sum(
    (movie.get("average_rating") or 0) * (movie.get("vote_count") or 0)
    for movie in MOVIES
) / max(1, _TOTAL_VOTES)
_BAYESIAN_MINIMUM_VOTES = load_query_rules()["bayesian_minimum_votes"]
_TOPIC_SUPPORT_SOURCE = None
_TOPIC_SUPPORT = None
_GENERATED_SYNOPSIS_RE = re.compile(
    r"^imdb rating \d+(?:\.\d+)?/10 from [\d,]+ votes; released in \d{4}\. genres:",
    re.IGNORECASE,
)
POSTER_PUBLIC_BASE_URL = poster_public_base_url()
if POSTER_PUBLIC_BASE_URL:
    for movie in MOVIES:
        movie["poster_url"] = poster_public_url(movie["id"])

# Built once at startup -- rebuilding per-request would be wasteful
# since the movie corpus doesn't change while the server is running.
CATALOG_PATH = Path(__file__).parent / "data" / "imdb_movies.json"
TFIDF_CACHE_PATH = Path(__file__).parent / "data" / "tfidf_cache.pkl"
LIKE_TFIDF_CACHE_PATH = Path(__file__).parent / "data" / "tfidf_like_cache.pkl"
CONCEPT_TABLE = load_or_rebuild_concept_table(MOVIES)
smart_search_module.CONCEPT_TABLE = CONCEPT_TABLE
_LOADED_TFIDF_MODEL = load_or_build_tfidf_model(MOVIES, TFIDF_CACHE_PATH, CATALOG_PATH)
_LOADED_LIKE_TFIDF_MODEL = load_or_build_tfidf_model(
    MOVIES,
    LIKE_TFIDF_CACHE_PATH,
    document_text_builder=movie_to_similarity_document_text,
)
_INDEX_MODELS_VALID = all(
    model
    and model.get("_catalog_version") == CATALOG_VERSION
    and model.get("_index_count") == len(MOVIES)
    for model in (_LOADED_TFIDF_MODEL, _LOADED_LIKE_TFIDF_MODEL)
)
_SEARCH_INDEX_FALLBACK = not (_INDEX_MODELS_VALID and CONCEPT_TABLE.get("enabled"))
_EMPTY_TFIDF_MODEL = {"idf": {}, "vectors": {}, "norms": {}}
TFIDF_MODEL = _LOADED_TFIDF_MODEL or _EMPTY_TFIDF_MODEL
LIKE_TFIDF_MODEL = _LOADED_LIKE_TFIDF_MODEL or _EMPTY_TFIDF_MODEL
if _SEARCH_INDEX_FALLBACK:
    app.logger.error("Catalog-derived search indexes are unavailable or stale; using keyword fallback.")
    TFIDF_MODEL = _EMPTY_TFIDF_MODEL
    LIKE_TFIDF_MODEL = _EMPTY_TFIDF_MODEL
INDEX_FALLBACK_NOTICE = (
    "Search indexes are unavailable or out of date; using plain keyword matching."
    if _SEARCH_INDEX_FALLBACK else ""
)
TITLE_INDEX = {}
TITLE_EXACT_INDEX = {}
TITLE_TOKEN_INDEX = {}
_NORMAL_SEARCH_HTML_CACHE = {}
NORMAL_SEARCH_HTML_CACHE_SIZE = 32
_SEARCH_CONCURRENCY = threading.BoundedSemaphore(1)


def _canonical_genre(value):
    normalized = normalize_title(value)
    aliases = {
        "science fiction": "sci-fi",
        "sci fi": "sci-fi",
        "science fi": "sci-fi",
        "biopic": "biography",
        "romcom": "comedy",
        "animated": "animation",
    }
    return aliases.get(normalized, normalized)


def _build_genre_postings(movies):
    postings = {}
    for movie in movies:
        movie_id = str(movie.get("id", ""))
        for genre in movie.get("genres") or []:
            canonical = _canonical_genre(genre)
            if canonical:
                postings.setdefault(canonical, set()).add(movie_id)
    return {genre: frozenset(movie_ids) for genre, movie_ids in postings.items()}


GENRE_MOVIE_IDS = _build_genre_postings(MOVIES)


def _title_key(value, fold_accents=True):
    if fold_accents:
        return normalize_title(value)
    text = unicodedata.normalize("NFC", str(value or "")).casefold()
    text = re.sub(r"[‘’']", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^(?:the|a|an)\s+", "", text)


for movie in MOVIES:
    normalized_title = _title_key(movie["title"])
    TITLE_EXACT_INDEX.setdefault(_title_key(movie["title"], fold_accents=False), []).append(movie)
    TITLE_INDEX.setdefault(normalized_title, []).append(movie)
    for alias in title_aliases(movie):
        TITLE_INDEX.setdefault(alias, []).append(movie)
    for token in sorted(set(normalized_title.split())):
        if len(token) >= 4:
            TITLE_TOKEN_INDEX.setdefault(token, []).append(movie)

SERIES_GROUPS = derive_series_groups(MOVIES)
RISKY_TITLE_VOCABULARY = title_risk_vocabulary(MOVIES)

# Warm catalog-derived parser/template structures once per process so the first
# user request does not pay the lazy-import and Jinja compilation cost.
extract_preferences("")
build_structured_query("")
app.jinja_env.get_template("index.html")
smart_search_module._get_bm25_index(MOVIES)


@app.route("/health", methods=["GET"])
def health():
    index_version = (
        _LOADED_TFIDF_MODEL.get("_catalog_version")
        if _LOADED_TFIDF_MODEL else None
    )
    payload = {
        "status": "ok" if not _SEARCH_INDEX_FALLBACK else "degraded",
        "catalog_count": len(MOVIES),
        "index_count": (
            _LOADED_TFIDF_MODEL.get("_index_count", 0)
            if _LOADED_TFIDF_MODEL else 0
        ),
        "catalog_version": CATALOG_VERSION,
        "index_version": index_version,
        "concept_count": len(CONCEPT_TABLE.get("concepts", {})),
        "keyword_fallback": _SEARCH_INDEX_FALLBACK,
    }
    return jsonify(payload), (200 if not _SEARCH_INDEX_FALLBACK else 503)

EXAMPLE_QUERIES = [
    "I want something suspenseful but not horror, preferably a mystery under two hours.",
    "I want something funny but not romance, around 100 minutes.",
    "Give me a scary movie but no gore, at least 100 minutes.",
    "Gusto ko ng suspenseful na mystery movie, less than 130 minutes, walang horror.",
]
PER_PAGE_OPTIONS = (12, 24, 48)
DEFAULT_PER_PAGE = 12


def _run_search_stage(
    name, operation, fallback, trace=None, *, complete_after_budget=False,
    fallback_factory=None,
):
    trace = trace or getattr(g, "search_trace", None)
    if trace is not None and trace.budget_exceeded and not complete_after_budget:
        trace.stage = name
        trace.report_budget_exceeded(name)
        trace.fallback(name)
        return fallback
    if trace is not None:
        trace.stage = name
        if trace.budget_exceeded:
            trace.report_budget_exceeded(name)
    started = time.perf_counter()
    try:
        value = operation()
    except Exception as error:
        if trace is not None:
            trace.error(name, error)
            trace.fallback(name)
        if fallback_factory is not None:
            return fallback_factory()
        return fallback
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if trace is not None:
            trace.stage_timings[name] = round(
                trace.stage_timings.get(name, 0.0) + elapsed_ms,
                3,
            )
    if trace is not None and trace.budget_exceeded:
        trace.report_budget_exceeded(name)
    return value


def _minimal_preferences(query, trace=None):
    try:
        return extract_preferences(query)
    except Exception as error:
        if trace is not None:
            trace.error("parse-fallback", error)
        return {
            "genres": [], "excluded_genres": [], "moods": [], "excluded_moods": [],
            "themes": [], "excluded_themes": [], "excluded_content_descriptors": [],
            "excluded_keywords": [], "free_text_keywords": [], "runtime": {
                "min": None, "max": None, "target": None,
            }, "release_year": None, "cast_gender": None,
        }


def _minimal_query_analysis(query, trace=None):
    preferences = _minimal_preferences(query, trace)
    try:
        normalized = normalize_text(query)
    except Exception as error:
        if trace is not None:
            trace.error("normalize-fallback", error)
        normalized = str(query).casefold()
    try:
        country, country_phrase = detect_country(normalized)
    except Exception as error:
        if trace is not None:
            trace.error("parse-fallback", error)
        country, country_phrase = "", ""
    unsupported = {"people": [], "languages": [], "certifications": []}
    return {
        "raw_query": query,
        "normalized_query": normalized,
        "country": country,
        "country_phrase": country_phrase,
        "ranking_intent": "",
        "ranking_phrase": "",
        "cleaned_text": normalized,
        "preferences": preferences,
        "semantic_description": preferences.get("free_text_keywords", []),
        "people": [],
        "languages": [],
        "unsupported_filters": unsupported,
        "debug": {
            "country": country,
            "ranking_intent": "",
            "genres": preferences.get("genres", []),
            "excluded_genres": preferences.get("excluded_genres", []),
            "moods": preferences.get("moods", []),
            "themes": preferences.get("themes", []),
            "release_year": preferences.get("release_year"),
            "semantic_description": preferences.get("free_text_keywords", []),
            "people": [],
            "languages": [],
            "cast_gender": preferences.get("cast_gender"),
        },
    }


def _minimal_understanding(query, query_analysis):
    preferences = query_analysis["preferences"]
    year = preferences.get("release_year") or {"min": None, "max": None}
    return {
        "raw_query": query,
        "core_intent": query_analysis.get("cleaned_text", query),
        "required_terms": list(preferences.get("free_text_keywords", [])),
        "keywords": list(preferences.get("free_text_keywords", [])),
        "expanded_concepts": [],
        "example_titles": [],
        "similar_to": None,
        "similar_to_movie_id": None,
        "genres": list(preferences.get("genres", [])),
        "mood_tone": list(preferences.get("moods", [])),
        "themes": list(preferences.get("themes", [])),
        "year_range": {"from": year.get("min"), "to": year.get("max")},
        "min_rating": None,
        "exclusions": [],
        "negative_requirements": [],
        "alternative_queries": [],
        "hard_constraints": [],
        "filters": {
            "genres": list(preferences.get("genres", [])),
            "year_range": {"from": year.get("min"), "to": year.get("max")},
            "runtime": dict(preferences.get("runtime") or {}),
            "excluded_genres": list(preferences.get("excluded_genres", [])),
            "excluded_moods": list(preferences.get("excluded_moods", [])),
            "excluded_themes": list(preferences.get("excluded_themes", [])),
            "excluded_content_descriptors": list(preferences.get("excluded_content_descriptors", [])),
            "excluded_keywords": list(preferences.get("excluded_keywords", [])),
        },
    }


def _emergency_search_response(query, notice=None):
    notice = notice or (
        "Search could not safely prepare results. No filters were relaxed. "
        "Remove a filter or try a shorter query."
    )
    context = dict(getattr(g, "search_context", None) or {})
    context.update({
        "query": str(query or "")[:MAX_QUERY_LENGTH],
        "search_notice": notice,
        "assistant_message": notice,
        "assistant_state": "empty",
        "selected_genre": "",
        "selected_genres": [],
        "selected_awards": [],
        "recognition_options": RECOGNITION_OPTIONS,
        "selected_nationality": "",
        "award_filter": False,
        "alternative_queries": [],
        "detected_nationality": "",
        "filter_conflicts": [],
        "similar_to_title": "",
        "similar_series_members": [],
        "like_title_suggestions": [],
        "nationality_label": "",
        "genres": sorted({
            _canonical_genre(genre)
            for movie in MOVIES for genre in movie.get("genres", [])
        }),
        "nationalities": sorted({
            code
            for movie in MOVIES for code in movie_country_codes(movie)
            if code
        }),
        "nationality_labels": COUNTRY_LABELS,
        "movie_origin_label": movie_origin_label,
        "movie_poster_url": movie_poster_url,
        "examples": EXAMPLE_QUERIES,
        "searched": True,
        "results": [],
        "movies": [],
        "result_count": 0,
        "result_count_label": "0 exact, 0 close, 0 related",
        "result_tier_counts": {"exact": 0, "close": 0, "related": 0},
        "page_number": 1,
        "total_pages": 1,
        "per_page": DEFAULT_PER_PAGE,
        "per_page_options": PER_PAGE_OPTIONS,
        "preference_tags": context.get("preference_tags", []),
        "enforced_exclusion_keys": [],
        "dropped": [],
        "feedback_saved": False,
        "debug_enabled": False,
        "debug_view": None,
        "query_debug": {},
    })
    try:
        response = make_response(render_template("index.html", **context), 200)
    except Exception as error:
        trace = getattr(g, "search_trace", None)
        if trace is not None:
            trace.error("render-fallback", error)
        response = make_response(
            "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
            "<title>Search unavailable</title><p>"
            "Search could not safely prepare results. No filters were relaxed. "
            "Remove a filter or try a shorter query.</p></html>",
            200,
        )
    g.search_context = context
    return response


def _resilient_search(view):
    def wrapped(*args, **kwargs):
        query = request.args.get("q", "")
        trace = SearchTrace(query)
        g.search_trace = trace
        g.search_context = None
        _SEARCH_CONCURRENCY.acquire()
        trace.deadline = time.perf_counter() + SEARCH_BUDGET_SECONDS
        try:
            try:
                response = make_response(view(*args, **kwargs))
            except Exception as error:
                trace.error(trace.stage, error)
                trace.fallback("request-error")
                response = _emergency_search_response(query)
            if response.status_code >= 500:
                trace.error("http-response", RuntimeError(f"Search returned HTTP {response.status_code}"))
                trace.fallback("http-response")
                response = _emergency_search_response(query)
            record = trace.finish(response.status_code, getattr(g, "search_context", None))
            write_search_record(record)
            return response
        finally:
            _SEARCH_CONCURRENCY.release()
    wrapped.__name__ = view.__name__
    return wrapped
NATIONALITY_ALIASES = {
    "american": "US", "british": "GB", "english": "GB", "indian": "IN",
    "japanese": "JP", "korean": "KR", "south korean": "KR", "chinese": "CN",
    "french": "FR", "german": "DE", "italian": "IT", "spanish": "ES",
    "mexican": "MX", "canadian": "CA", "australian": "AU", "brazilian": "BR",
    "argentinian": "AR", "russian": "RU", "ukrainian": "UA", "polish": "PL",
    "turkish": "TR", "dutch": "NL", "belgian": "BE", "swedish": "SE",
    "danish": "DK", "norwegian": "NO", "finnish": "FI", "irish": "IE",
    "scottish": "GB", "welsh": "GB", "new zealand": "NZ", "south african": "ZA",
    "nigerian": "NG", "iranian": "IR", "israeli": "IL", "thai": "TH",
    "vietnamese": "VN", "filipino": "PH", "indonesian": "ID", "malaysian": "MY",
    "singaporean": "SG", "colombian": "CO", "chilean": "CL", "peruvian": "PE",
    "czech": "CZ", "hungarian": "HU", "romanian": "RO", "greek": "GR",
    "portuguese": "PT", "austrian": "AT", "swiss": "CH", "icelandic": "IS",
}


def country_labels():
    labels = {}
    for country in pycountry.countries:
        labels[country.alpha_2] = country.name
    labels.update({"CV": "Cabo Verde", "XK": "Kosovo", "SU": "Soviet Union"})
    return labels


COUNTRY_LABELS = country_labels()
COUNTRY_PHRASES = {
    normalize_text(name).replace(",", " ").strip(): code
    for code, name in COUNTRY_LABELS.items()
}


def detect_nationality(query):
    return detect_country(normalize_text(query).replace(",", " ").strip())[0]


def remove_nationality_text(query, code):
    """Remove the detected country phrase before local NLP preference parsing."""
    normalized = normalize_text(query).replace(",", " ")
    phrases = [
        phrase for phrase, alias_code in NATIONALITY_ALIASES.items()
        if alias_code == code
    ]
    country_name = COUNTRY_LABELS.get(code, "").lower()
    if country_name:
        phrases.append(country_name)
    for phrase in sorted(phrases, key=lambda value: (-len(value), value)):
        normalized = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", normalized)
    return " ".join(normalized.split())


def remove_nationality_words(keywords, query, code):
    """Remove origin words from keyword scoring after origin detection."""
    normalized = normalize_text(query).replace(",", " ")
    removable = set()
    for phrase, alias_code in NATIONALITY_ALIASES.items():
        if alias_code == code and phrase in normalized:
            removable.update(phrase.split())
    country_name = COUNTRY_LABELS.get(code, "").lower()
    if country_name and country_name in normalized:
        removable.update(country_name.split())
    return [word for word in keywords if word not in removable]


def poster_exists(movie):
    """Return whether the optional JPEG poster for a movie is installed."""
    poster_path = Path(app.static_folder) / "images" / f"{movie['id']}.jpg"
    return poster_path.is_file()


def movie_poster_url(movie):
    poster_url = movie.get("poster_url")
    if POSTER_PUBLIC_BASE_URL and isinstance(poster_url, str) and poster_url.startswith(POSTER_PUBLIC_BASE_URL + "/"):
        return poster_url
    poster_path = movie.get("poster_path")
    if isinstance(poster_path, str) and poster_path.startswith("/"):
        return f"https://image.tmdb.org/t/p/w342{poster_path}"
    if isinstance(poster_url, str) and poster_url.startswith((
        "https://commons.wikimedia.org/wiki/Special:FilePath/",
        "https://upload.wikimedia.org/wikipedia/commons/",
    )):
        return poster_url
    return ""


def build_match_reasons(movie, preferences, breakdown, satisfied=(), cast_supported=False):
    reasons = explain_match(movie, preferences, breakdown)
    existing = {reason["text"].casefold() for reason in reasons}
    genre_values = movie.get("genres") or []
    if isinstance(genre_values, str):
        genre_values = [genre_values]
    actual_genres = {
        normalize_title(genre)
        for genre in genre_values
        if isinstance(genre, str)
    }
    for genre in preferences.get("genres", []):
        if not actual_genres.intersection(_concept_forms(GENRE_SYNONYMS, genre)):
            text = f"Missing: {title_case(genre)} genre"
            if text.casefold() not in existing:
                reasons.append({"ok": False, "text": text})
                existing.add(text.casefold())
    for detail in satisfied:
        text = f"Matched: {detail}"
        if text.casefold() not in existing:
            reasons.append({"ok": True, "text": text})
            existing.add(text.casefold())
    if cast_supported:
        reasons.append({"ok": True, "text": "Cast evidence supports your request"})
    return reasons


def movie_origin_label(movie):
    countries = movie.get("production_countries") or []
    if isinstance(countries, str):
        countries = [countries]
    labels = [COUNTRY_LABELS.get(code, code) for code in countries if isinstance(code, str) and code]
    return ", ".join(dict.fromkeys(labels)) if labels else ""


def movie_rank_key(movie):
    return (
        movie.get("vote_count", 0),
        movie.get("average_rating") or 0,
        movie.get("release_year", 0),
        str(movie.get("title", "")).casefold(),
        str(movie.get("id", "")),
    )


# key -> (label, case-folded award text to look for; empty means any recorded award)
RECOGNITION_OPTIONS = {
    "winners": ("Major award winners", ""),
    "academy": ("Academy Award", "academy award"),
    "palme": ("Palme d'Or", "palme d'or"),
    "golden-bear": ("Golden Bear", "golden bear"),
    "golden-lion": ("Golden Lion", "golden lion"),
}


def movie_matches_recognition(movie, selected_keys):
    """True when the movie has a curated award matching any selected recognition option."""
    awards = [str(award).casefold() for award in movie.get("notable_awards") or []]
    if not awards:
        return False
    return any(
        not RECOGNITION_OPTIONS[key][1] or any(RECOGNITION_OPTIONS[key][1] in award for award in awards)
        for key in selected_keys
    )


def _concept_forms(table, tag):
    normalized_tag = normalize_title(tag)
    forms = {normalized_tag} if normalized_tag else set()
    for canonical, synonyms in table.items():
        canonical_forms = {
            normalize_title(value)
            for value in (canonical, *synonyms)
            if normalize_title(value)
        }
        if normalized_tag in canonical_forms:
            forms.update(canonical_forms)
    return forms


def _final_gate_phrase(value):
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text))


@lru_cache(maxsize=2048)
def _final_gate_exclusion_forms(tag):
    forms = {_final_gate_phrase(tag)}
    tables = (
        GENRE_SYNONYMS,
        MOOD_SYNONYMS,
        THEME_SYNONYMS,
        CONTENT_DESCRIPTOR_SYNONYMS,
    )
    changed = True
    while changed:
        changed = False
        for table in tables:
            for canonical, synonyms in table.items():
                linked = {
                    _final_gate_phrase(value)
                    for value in (canonical, *synonyms)
                    if _final_gate_phrase(value)
                }
                if forms.intersection(linked) and not linked.issubset(forms):
                    forms.update(linked)
                    changed = True
    return frozenset(forms)


def _final_gate_values(value):
    return [value] if isinstance(value, str) else value if isinstance(value, list) else []


def _final_gate_violations(movie, preferences):
    if not preferences:
        return []
    tagged_fields = (
        "genres", "mood_tags", "themes", "keywords", "content_descriptors",
    )
    text_fields = ("synopsis", "overview", "tagline")
    tagged_values = {
        _final_gate_phrase(value)
        for field in tagged_fields
        for value in _final_gate_values(movie.get(field))
        if isinstance(value, str) and _final_gate_phrase(value)
    }
    text = " " + " ".join(
        _final_gate_phrase(movie.get(field))
        for field in text_fields
        if movie.get(field)
    ) + " "
    violations = []
    for key in (
        "excluded_genres", "excluded_moods", "excluded_themes",
        "excluded_content_descriptors", "excluded_keywords",
    ):
        for exclusion in preferences.get(key, []):
            forms = _final_gate_exclusion_forms(exclusion)
            if any(form in tagged_values for form in forms) or any(
                f" {form} " in text for form in forms
            ):
                violations.append(f"{key}:{exclusion}")
    return violations


def _excluded_genre_movie_ids(preferences, movies):
    if not preferences:
        return set()
    postings = GENRE_MOVIE_IDS if movies is _CATALOG_SOURCE else _build_genre_postings(movies)
    excluded = set()
    for genre in preferences.get("excluded_genres", []):
        forms = _concept_forms(GENRE_SYNONYMS, genre)
        for indexed_genre, movie_ids in postings.items():
            if normalize_title(indexed_genre) in forms:
                excluded.update(movie_ids)
    return excluded


def find_filter_conflicts(preferences, selected_genres=()):
    """Report include/exclude and impossible range combinations from parsed filters."""
    includes = [
        (category, tag, table)
        for category, key, table in (
            ("genre", "genres", GENRE_SYNONYMS),
            ("mood", "moods", MOOD_SYNONYMS),
            ("theme", "themes", THEME_SYNONYMS),
        )
        for tag in preferences.get(key, [])
    ]
    includes.extend(("genre", tag, GENRE_SYNONYMS) for tag in selected_genres)
    excludes = [
        (category, tag, table)
        for category, key, table in (
            ("genre", "excluded_genres", GENRE_SYNONYMS),
            ("mood", "excluded_moods", MOOD_SYNONYMS),
            ("theme", "excluded_themes", THEME_SYNONYMS),
            ("content", "excluded_content_descriptors", CONTENT_DESCRIPTOR_SYNONYMS),
        )
        for tag in preferences.get(key, [])
    ]
    messages = []
    for include_category, include_tag, include_table in includes:
        include_forms = _concept_forms(include_table, include_tag)
        for exclude_category, exclude_tag, exclude_table in excludes:
            if include_forms.intersection(_concept_forms(exclude_table, exclude_tag)):
                message = (
                    f"Conflict: {title_case(include_tag)} is both included and excluded; "
                    f"exclusions win. Remove either filter below."
                )
                if message not in messages:
                    messages.append(message)
    runtime = preferences.get("runtime") or {}
    if runtime.get("min") is not None and runtime.get("max") is not None and runtime["min"] > runtime["max"]:
        messages.append("Conflict: minimum runtime exceeds maximum runtime. Remove either side below.")
    year = preferences.get("release_year") or {}
    if year.get("min") is not None and year.get("max") is not None and year["min"] > year["max"]:
        messages.append("Conflict: minimum year exceeds maximum year. Remove either side below.")
    return messages


def ranking_key(entry, ranking_intent):
    movie = entry["movie"]
    rating = movie.get("average_rating") or 0
    votes = movie.get("vote_count") or 0
    year = movie.get("release_year") or 0
    weighted_prior = _BAYESIAN_RATING_PRIOR
    minimum_votes = _BAYESIAN_MINIMUM_VOTES
    adjusted_rating = (
        (votes / (votes + minimum_votes)) * rating
        + (minimum_votes / (votes + minimum_votes)) * weighted_prior
    ) if votes + minimum_votes else weighted_prior
    if ranking_intent == "popular":
        return (votes, rating, entry["percent"], year)
    if ranking_intent == "best":
        return (adjusted_rating, votes, entry["percent"], year)
    if ranking_intent == "worst":
        return (-adjusted_rating, votes, entry["percent"], year)
    return (entry["percent"], rating, votes, year)


def build_debug_view(query_analysis, preferences, filtered_count, result_count):
    """Create an inspectable summary of the local query interpretation."""
    country = query_analysis["country"]
    normalized = query_analysis["normalized_query"]
    if country and query_analysis["country_phrase"]:
        normalized = re.sub(
            rf"(?<![a-z]){re.escape(query_analysis['country_phrase'])}(?![a-z])",
            COUNTRY_LABELS.get(country, country).lower(),
            normalized,
        )
    release_year = preferences["release_year"] or {"min": None, "max": None}
    cast_constraint = preferences.get("cast_gender")
    structured = {
        "country": COUNTRY_LABELS.get(country, None) if country else None,
        "genres": [title_case(genre) for genre in preferences["genres"]],
        "excluded_genres": [title_case(genre) for genre in preferences["excluded_genres"]],
        "moods": [title_case(mood) for mood in preferences["moods"]],
        "themes": [title_case(theme) for theme in preferences["themes"]],
        "excluded_content_descriptors": preferences.get("excluded_content_descriptors", []),
        "year": release_year,
        "ranking": query_analysis["ranking_intent"] or None,
        "semantic_description": query_analysis["semantic_description"] or None,
        "people": query_analysis["people"] or None,
        "languages": query_analysis["languages"] or None,
        "cast_gender": cast_constraint,
    }
    filter_results = []
    if country:
        filter_results.append(("Country", "PASS", f"{COUNTRY_LABELS.get(country, country)}; {filtered_count:,} catalog candidates"))
    if preferences["genres"]:
        filter_results.append(("Genre", "PASS", ", ".join(title_case(genre) for genre in preferences["genres"])))
    if release_year["min"] is not None or release_year["max"] is not None:
        filter_results.append(("Year", "PASS", f"{release_year['min'] or 'any'} to {release_year['max'] or 'any'}"))
    if preferences["excluded_genres"]:
        filter_results.append(("Exclusions", "PASS", ", ".join(title_case(genre) for genre in preferences["excluded_genres"])))
    if preferences.get("excluded_content_descriptors"):
        filter_results.append(("Content exclusions", "PASS", ", ".join(preferences["excluded_content_descriptors"])))
    if preferences.get("cast_gender"):
        filter_results.append(("Cast gender", "PASS", str(preferences["cast_gender"])))
    ranking = query_analysis["ranking_intent"]
    ranking_order = "Relevance"
    if ranking == "best":
        ranking_order = "Explicit filters -> Rating -> Popularity -> Relevance"
    elif ranking == "popular":
        ranking_order = "Explicit filters -> Popularity -> Rating -> Relevance"
    elif ranking == "recommended":
        ranking_order = "Explicit filters -> Relevance -> Rating -> Popularity"
    return {
        "raw_query": query_analysis["raw_query"],
        "normalized_query": normalized,
        "intent": query_analysis["ranking_intent"] or "none",
        "country": COUNTRY_LABELS.get(country, "none") if country else "none",
        "genres": ", ".join(title_case(genre) for genre in preferences["genres"]) or "none",
        "excluded_genres": ", ".join(title_case(genre) for genre in preferences["excluded_genres"]) or "none",
        "excluded_content_descriptors": ", ".join(preferences.get("excluded_content_descriptors", [])) or "none",
        "unsupported_filters": query_analysis["unsupported_filters"] or None,
        "year": release_year if release_year["min"] is not None or release_year["max"] is not None else "none",
        "semantic_description": ", ".join(query_analysis["semantic_description"]) or "none",
        "structured_json": json.dumps(structured, indent=2),
        "filter_results": filter_results,
        "ranking_order": ranking_order,
        "result_count": result_count,
    }


def movie_country_codes(movie):
    """Use TMDB production countries when enriched, otherwise IMDb listing regions."""
    return movie.get("production_countries") or movie.get("origin_regions", [movie.get("nationality")])


def build_entity_index(movies):
    """Build normalized indexes for every searchable catalog entity field."""
    fields = {
        "cast": ("cast", "actors"),
        "director": ("director", "directors"),
        "writer": ("writer", "writers"),
        "language": ("original_language", "language"),
        "certification": ("certification", "content_rating", "rating_certificate"),
        "award": ("notable_awards",),
    }
    indexes = {name: {} for name in (*fields, "country")}
    for movie in movies:
        movie_id = str(movie.get("id"))
        for entity_type, keys in fields.items():
            for key in keys:
                values = movie.get(key) or []
                if isinstance(values, str):
                    values = [values]
                for value in values:
                    name = value.get("name", "") if isinstance(value, dict) else value
                    normalized = normalize_title(name)
                    if normalized:
                        indexes[entity_type].setdefault(normalized, set()).add(movie_id)
                        if entity_type == "language":
                            try:
                                language = pycountry.languages.lookup(name)
                            except LookupError:
                                language = None
                            if language:
                                for language_name in (getattr(language, "name", ""), getattr(language, "alpha_2", "")):
                                    language_key = normalize_title(language_name)
                                    if language_key:
                                        indexes[entity_type].setdefault(language_key, set()).add(movie_id)
        for code in movie_country_codes(movie) or []:
            normalized = str(code).strip().upper()
            if normalized:
                indexes["country"].setdefault(normalized, set()).add(movie_id)
        for key in RECOGNITION_OPTIONS:
            if movie_matches_recognition(movie, [key]):
                indexes["award"].setdefault(key, set()).add(movie_id)
    return indexes


ENTITY_INDEX = build_entity_index(MOVIES)
_CATALOG_SOURCE = MOVIES
CATALOG_FIELD_COVERAGE = {
    "cast": sum(bool(movie.get("cast") or movie.get("actors")) for movie in MOVIES),
    "director": sum(bool(movie.get("director") or movie.get("directors")) for movie in MOVIES),
    "writer": sum(bool(movie.get("writer") or movie.get("writers")) for movie in MOVIES),
    "country": sum(bool(movie_country_codes(movie)) for movie in MOVIES),
    "language": sum(bool(movie.get("original_language") or movie.get("language")) for movie in MOVIES),
    "award": sum(bool(movie.get("notable_awards")) for movie in MOVIES),
    "certification": sum(bool(
        movie.get("certification") or movie.get("content_rating") or movie.get("rating_certificate")
    ) for movie in MOVIES),
}


def movie_matches_release_year(movie, constraint):
    """Apply an explicit release-year request as a hard catalog filter."""
    if not constraint:
        return True
    return evaluate_movie_constraints(
        movie, {"release_year": constraint},
    )["status"] == "satisfied"


GENERIC_TITLE_TOKENS = {
    "where", "there", "here", "when", "what", "which", "who", "why", "how",
    "movie", "movies", "film", "films", "is", "are", "was", "were", "am",
    "be", "been", "being", "a", "an", "the", "do", "does", "did",
    "have", "has", "had", "would", "could", "should",
}


def _find_title_matches_uncached(query, movies):
    """Find titles written exactly or as a phrase inside a natural-language query."""
    normalized_query = _title_key(query)
    risky_words = TITLE_STOP_WORDS | {
        "movie", "movies", "film", "films", "genre", "year",
    }
    spans = detect_title_spans(query, movies, risky_words, RISKY_TITLE_VOCABULARY)
    spans = _filter_entity_title_spans(query, spans)
    if spans and not any(span["whole_query"] for span in spans):
        prefix_key = _title_key(query).strip()
        if len(prefix_key.split()) >= 2:
            prefix_matches = [
                movie for movie in movies
                if any(alias.startswith(prefix_key) for alias in title_aliases(movie))
            ]
            if prefix_matches:
                return sorted(
                    prefix_matches,
                    key=lambda movie: (
                        -(movie.get("vote_count") or 0),
                        str(movie.get("title", "")).casefold(),
                        str(movie.get("id", "")),
                    ),
                )
    if spans:
        span = spans[0]
        named = list(span["movies"])
        literal_title_matches = [
            movie for movie in named
            if re.search(
                rf"(?<!\w){re.escape(unicodedata.normalize('NFC', movie.get('title', '')).casefold())}(?!\w)",
                unicodedata.normalize("NFC", str(query)).casefold(),
                flags=re.UNICODE,
            )
        ]
        if literal_title_matches:
            named = literal_title_matches
        if span["whole_query"]:
            exact_key = " ".join(
                word for word in _title_key(query, fold_accents=False).split()
                if word not in {"movie", "movies", "film", "films"}
            )
            exact_records = TITLE_EXACT_INDEX.get(exact_key, [])
            if exact_records:
                available_ids = {str(movie.get("id")) for movie in movies}
                named = [
                    movie for movie in exact_records
                    if str(movie.get("id")) in available_ids
                ]
            else:
                canonical_matches = [
                    movie for movie in named
                    if normalize_title(movie.get("title", "")) == span["alias"]
                ]
                if canonical_matches:
                    named = canonical_matches
            query_year = re.search(r"\b((?:18|19|20)\d{2})\b", str(query))
            year = int(query_year.group(1)) if query_year else None
            if year:
                year_matches = [movie for movie in named if movie.get("release_year") == year]
                if year_matches:
                    named = year_matches
            return named
        if span["explicit"] or len(span["alias"].replace(" ", "")) > 3:
            return named

    query_year = re.search(r"\b((?:18|19|20)\d{2})\b", str(query))
    query_year = int(query_year.group(1)) if query_year else None

    groups = SERIES_GROUPS if movies is MOVIES else derive_series_groups(movies)
    for group in groups:
        members = [
            movie for movie in movies
            if movie.get("title") in group["titles"]
        ]
        matching_members = [
            movie for movie in members
            if normalized_query in title_aliases(movie)
        ]
        if matching_members:
            named = matching_members[0]
            if query_year:
                named = next((movie for movie in matching_members if movie.get("release_year") == query_year), named)
            ordered = [named] + sorted(
                (movie for movie in members if movie is not named),
                key=lambda movie: (
                    movie.get("release_year") or 0,
                    -(movie.get("vote_count") or 0),
                    str(movie.get("title", "")).casefold(),
                    str(movie.get("id", "")),
                ),
            )
            return ordered

    for group in groups:
        members = [movie for movie in movies if movie.get("title") in group["titles"]]
        base_names = {
            normalize_title(group["base"]),
            *(normalize_title(movie.get("title", "")) for movie in members),
        }
        if normalized_query in base_names:
            exact_member = next((
                movie for movie in members
                if normalize_title(movie.get("title", "")) == normalized_query
            ), None)
            ordered = ([exact_member] if exact_member else []) + sorted(
                (movie for movie in members if movie is not exact_member),
                key=lambda movie: (
                    movie.get("release_year") or 0,
                    -(movie.get("vote_count") or 0),
                    str(movie.get("title", "")).casefold(),
                    str(movie.get("id", "")),
                ),
            )
            return ordered

    exact_matches = []
    for movie in movies:
        if normalized_query in title_aliases(movie):
            exact_matches.append(movie)
    if exact_matches:
        if query_year:
            exact_matches.sort(key=lambda movie: (
                movie.get("release_year") != query_year,
                -(movie.get("vote_count") or 0),
                str(movie.get("title", "")).casefold(),
                str(movie.get("id", "")),
            ))
        else:
            exact_matches.sort(key=lambda movie: (
                -(movie.get("vote_count") or 0),
                str(movie.get("title", "")).casefold(),
                str(movie.get("id", "")),
            ))
        return list({str(movie["id"]): movie for movie in exact_matches}.values())

    groups = SERIES_GROUPS if movies is MOVIES else derive_series_groups(movies)
    for group in groups:
        base_candidates = {
            normalize_title(title)
            for title in group["titles"]
        }
        if normalized_query not in base_candidates:
            continue
        member_titles = group["titles"]
        members = [
            movie for movie in movies
            if movie.get("title") in member_titles
        ]
        if exact_matches:
            return exact_matches
        named_entry = next((
            movie for movie in members
            if normalize_title(movie.get("title", "")) == normalized_query
        ), None)
        if named_entry:
            return [named_entry] + sorted(
                (movie for movie in members if movie is not named_entry),
                key=lambda movie: (
                    movie.get("release_year") or 0,
                    -(movie.get("vote_count") or 0),
                    str(movie.get("title", "")).casefold(),
                    str(movie.get("id", "")),
                ),
            )
        base_query = normalized_query
        base_members = sorted(
            members,
            key=lambda movie: (
                normalize_title(movie.get("title", "")) != base_query,
                movie.get("release_year") or 0,
                -(movie.get("vote_count") or 0),
                str(movie.get("title", "")).casefold(),
                str(movie.get("id", "")),
            ),
        )
        return base_members

    title_prefix = normalized_query.strip()
    if len(title_prefix.split()) >= 2:
        prefix_matches = [
            movie for movie in movies
            if normalize_title(movie["title"]).startswith(title_prefix)
        ]
        if prefix_matches:
            return sorted(
                prefix_matches,
                key=lambda movie: (
                    -(movie.get("vote_count") or 0),
                    str(movie.get("title", "")).casefold(),
                    str(movie.get("id", "")),
                ),
            )

    exact_query = _title_key(query, fold_accents=False).strip()
    exact_matches = TITLE_EXACT_INDEX.get(exact_query, [])
    if not exact_matches:
        exact_matches = TITLE_INDEX.get(normalized_query.strip(), [])
    if exact_matches:
        allowed_ids = {movie["id"] for movie in movies}
        return [movie for movie in exact_matches if movie["id"] in allowed_ids]

    without_leading_article = re.sub(r"^(?:the|a|an)\s+", "", exact_query)
    if without_leading_article != exact_query:
        exact_matches = TITLE_EXACT_INDEX.get(without_leading_article, [])
        if exact_matches:
            allowed_ids = {movie["id"] for movie in movies}
            return [movie for movie in exact_matches if movie["id"] in allowed_ids]

    normalized_without_leading_article = re.sub(
        r"^(?:the|a|an)\s+", "", normalized_query.strip(),
    )
    exact_matches = TITLE_INDEX.get(normalized_without_leading_article, [])
    if exact_matches:
        allowed_ids = {movie["id"] for movie in movies}
        return [movie for movie in exact_matches if movie["id"] in allowed_ids]

    title_prefix = normalized_query.strip()
    if len(title_prefix.split()) >= 2 and len(title_prefix) >= 4:
        prefix_matches = [
            movie for movie in movies
            if _title_key(movie["title"]).startswith(title_prefix)
        ]
        if prefix_matches:
            return list({movie["id"]: movie for movie in prefix_matches}.values())

    if re.search(r"\b(?:no|not|without|except|excluding|avoid|avoiding|skip|never)\b", normalized_query):
        return []
    query_tokens = [
        token for token in normalized_query.split()
        if len(token) >= 2 and token not in GENERIC_TITLE_TOKENS
    ]
    candidate_lists = [TITLE_TOKEN_INDEX[token] for token in query_tokens if token in TITLE_TOKEN_INDEX]
    candidates = min(candidate_lists, key=len) if candidate_lists else movies
    allowed_ids = {movie["id"] for movie in movies}
    matches = []
    for movie in candidates:
        if movie["id"] not in allowed_ids:
            continue
        normalized_title = _title_key(movie["title"])
        title_pattern = rf"(?<![a-z0-9]){re.escape(normalized_title)}(?![a-z0-9])"
        title_words = normalized_title.split()
        explicitly_named = re.search(rf"\b(?:called|named|titled)\s+{re.escape(normalized_title)}\b", normalized_query)
        has_filter_clause = re.search(
            r"\b(?:under|below|less than|over|more than|at least|no more than|"
            r"not longer than|before|after|since|between|from|around|without|"
            r"excluding|except|not|no)\b|\b(?:19|20)\d{2}\b",
            normalized_query,
        )
        if len(title_words) >= 2 and re.search(title_pattern, normalized_query):
            matches.append(movie)
        elif len(query_tokens) >= 2 and normalized_title.startswith(" ".join(query_tokens)):
            matches.append(movie)
        elif len(title_words) == 1 and (explicitly_named or has_filter_clause) and re.search(title_pattern, normalized_query):
            matches.append(movie)
    if not matches:
        return []
    matches = list({movie["id"]: movie for movie in matches}.values())
    exact_query = _title_key(query, fold_accents=False)
    literal_matches = [
        movie for movie in matches
        if re.search(
            rf"(?<![a-z0-9]){re.escape(_title_key(movie['title'], fold_accents=False))}(?![a-z0-9])",
            exact_query,
        )
    ]
    if literal_matches:
        matches = literal_matches
    title_query_key = _title_key(query)
    title_query_without_article = re.sub(r"^(?:the|a|an)\s+", "", title_query_key)
    title_context_cues = {
        "after", "around", "at", "before", "between", "except", "excluding",
        "from", "less", "more", "no", "not", "over", "since", "under",
        "until", "without",
    }
    explicit_title_context = is_exact_title_query(query, matches)
    for movie in matches:
        title = _title_key(movie["title"])
        title_variants = [title]
        title_without_article = re.sub(r"^(?:the|a|an)\s+", "", title)
        if title_without_article != title:
            title_variants.append(title_without_article)
        query_variants = [title_query_key, title_query_without_article]
        for query_variant in query_variants:
            for title_variant in title_variants:
                if not query_variant.startswith(title_variant + " "):
                    continue
                remainder = query_variant[len(title_variant):].split()
                if remainder and remainder[0] in title_context_cues:
                    explicit_title_context = True
                elif remainder:
                    suffix_preferences = build_structured_query(" ".join(remainder))["preferences"]
                    if any((
                        suffix_preferences["genres"],
                        suffix_preferences["moods"],
                        suffix_preferences["themes"],
                        suffix_preferences["runtime"]["min"],
                        suffix_preferences["runtime"]["max"],
                        suffix_preferences["runtime"]["target"],
                        suffix_preferences["release_year"],
                    )):
                        explicit_title_context = True
                    else:
                        suffix_tokens = set(remainder) - GENERIC_TITLE_TOKENS
                        movie_text = _title_key(" ".join((
                            movie.get("synopsis") or "",
                            *(movie.get("genres") or []),
                            *(movie.get("mood_tags") or []),
                            *(movie.get("themes") or []),
                            *(movie.get("keywords") or []),
                        )))
                        if suffix_tokens.intersection(movie_text.split()):
                            explicit_title_context = True
        title_pattern = re.escape(title)
        if re.search(
            rf"\b(?:called|named|titled|like|similar to)\s+(?:the\s+)?{title_pattern}\b",
            title_query_key,
        ):
            explicit_title_context = True
    if not explicit_title_context:
        return []
    longest_title_length = max(
        len(_title_key(movie["title"]).split())
        for movie in matches
    )
    return [
        movie for movie in matches
        if len(_title_key(movie["title"]).split()) == longest_title_length
    ]


_FIND_TITLE_MATCH_CACHE = {}


def find_title_matches(query, movies):
    cache_key = (
        CATALOG_VERSION["record_count"],
        CATALOG_VERSION["content_hash"],
        normalize_text(str(query or "")),
        id(movies),
    )
    cached = _FIND_TITLE_MATCH_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    matches = tuple(_find_title_matches_uncached(query, movies))
    if len(_FIND_TITLE_MATCH_CACHE) >= 512:
        oldest_key = next(iter(_FIND_TITLE_MATCH_CACHE))
        del _FIND_TITLE_MATCH_CACHE[oldest_key]
    _FIND_TITLE_MATCH_CACHE[cache_key] = matches
    return list(matches)


def _filter_entity_title_spans(query, spans):
    normalized = normalize_text(query)
    people, languages = detect_people_and_language(normalized)
    cast_constraint = detect_cast_gender_constraint(normalized)
    entity_values = []
    for value in [*people, *languages]:
        parsed = extract_preferences(value)
        if cast_constraint or not any((
            parsed["genres"], parsed["moods"], parsed["themes"],
        )):
            entity_values.append(normalize_title(value))
    if not entity_values:
        return spans
    filtered = []
    for span in spans:
        if span["whole_query"] or span["explicit"]:
            filtered.append(span)
            continue
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(span['alias'])}(?![a-z0-9])", value)
            for value in entity_values
        ):
            continue
        filtered.append(span)
    return filtered


def remove_title_text(query, title_matches):
    """Remove a detected title so its words cannot become search preferences."""
    normalized = normalize_text(query)
    for movie in title_matches:
        for title in sorted(title_aliases(movie), key=lambda value: (-len(value), value)):
            normalized_title = normalize_text(title)
            normalized = re.sub(
                rf"(?<![a-z0-9]){re.escape(normalized_title)}(?![a-z0-9])",
                " ",
                normalized,
                flags=re.IGNORECASE,
            )
    return " ".join(normalized.split())


def is_exact_title_query(query, title_matches):
    """True when the entire query is one movie title, ignoring case and punctuation."""
    if any(
        span["whole_query"]
        for span in detect_title_spans(
            query, title_matches, TITLE_STOP_WORDS, RISKY_TITLE_VOCABULARY,
        )
    ):
        return True
    normalized_query = _title_key(query)
    words = normalized_query.split()
    words_without_fillers = [
        word for word in words if word not in {"movie", "movies", "film", "films"}
    ]
    query_without_year = [
        word for word in words_without_fillers
        if not re.fullmatch(r"(?:18|19|20)\d{2}", word)
    ]
    normalized_without_fillers = " ".join(words_without_fillers)
    normalized_without_article = " ".join(query_without_year)
    return any(
        normalized_query in title_aliases(movie)
        or normalized_without_fillers in title_aliases(movie)
        or normalized_without_article in title_aliases(movie)
        for movie in title_matches
    )


def _exact_title_query_candidates(query):
    normalized = _title_key(query)
    words = normalized.split()
    keys = [normalized]
    without_fillers = " ".join(
        word for word in words if word not in {"movie", "movies", "film", "films"}
    )
    keys.append(without_fillers)
    for key in tuple(keys):
        keys.append(re.sub(r"^(?:the|a|an)\s+", "", key))
    matches = []
    seen = set()
    for key in keys:
        for movie in TITLE_INDEX.get(key, []):
            movie_id = str(movie.get("id", ""))
            if movie_id not in seen:
                matches.append(movie)
                seen.add(movie_id)
    return matches


def _contains_complete_title(query, title):
    normalized_query = _title_key(query)
    normalized_title = normalize_title(title)
    if normalized_query == normalized_title:
        return True
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_title)}(?![a-z0-9])",
        normalized_query,
    ))


def has_unsupported_negated_requirement(query):
    """Return True when a negated descriptor cannot be verified by the catalog data."""
    if not query:
        return False
    normalized = normalize_text(query).replace(",", " ")
    return bool(re.search(
        r"\b(?:no|not|without|except|excluding|avoid|avoiding|skip|never)\b.*\b(?:woman|women|female|male|man|men|girl|girls|boy|boys|wife|husband|mother|father|child|children)\b",
        normalized,
        flags=re.IGNORECASE,
    ))


def drop_constraints(preferences, understanding, drops):
    """Remove user-dismissed constraints from the parsed query before searching."""
    lists = {
        "genre": ("genres", "genres"), "mood": ("moods", "mood_tone"),
        "theme": ("themes", None), "exclude_genre": ("excluded_genres", "exclusions"),
        "exclude_mood": ("excluded_moods", "exclusions"),
        "exclude_theme": ("excluded_themes", None),
        "exclude_content_descriptor": ("excluded_content_descriptors", None),
        "exclude_keyword": ("excluded_keywords", None),
    }
    for drop in drops:
        kind, _, value = drop.partition(":")
        if kind in lists:
            pref_key, understanding_key = lists[kind]
            preferences[pref_key] = [item for item in preferences[pref_key] if item != value]
            if understanding and understanding_key:
                understanding[understanding_key] = [item for item in understanding[understanding_key] if item != value]
            if understanding and kind == "genre":
                understanding["filters"]["genres"] = [g for g in understanding["filters"]["genres"] if g != value]
            if understanding and kind.startswith("exclude"):
                constraint = (
                    f"exclude keyword {value}" if kind == "exclude_keyword"
                    else f"exclude {value}"
                )
                understanding["hard_constraints"] = [
                    c for c in understanding["hard_constraints"] if c != constraint
                ]
                understanding["negative_requirements"] = [
                    item for item in understanding.get("negative_requirements", []) if item != value
                ]
                if pref_key in understanding["filters"]:
                    understanding["filters"][pref_key] = [
                        item for item in understanding["filters"].get(pref_key, [])
                        if item != value
                    ]
        elif kind == "runtime":
            preferences["runtime"] = {**preferences["runtime"], "min": None, "max": None, "target": None}
            if understanding:
                understanding["filters"]["runtime"] = {
                    "min": None, "max": None, "target": None,
                }
        elif kind == "runtime_min":
            preferences["runtime"]["min"] = None
        elif kind == "runtime_max":
            preferences["runtime"]["max"] = None
        elif kind == "year":
            preferences["release_year"] = None
            if understanding:
                understanding["year_range"] = {"from": None, "to": None}
                understanding["filters"]["year_range"] = {"from": None, "to": None}
                understanding["hard_constraints"] = [c for c in understanding["hard_constraints"] if c != "release year"]
        elif kind == "year_min" and preferences.get("release_year"):
            preferences["release_year"]["min"] = None
        elif kind == "year_max" and preferences.get("release_year"):
            preferences["release_year"]["max"] = None
        elif kind == "soft_exclusion":
            preferences["soft_exclusions"] = [
                item for item in preferences.get("soft_exclusions", []) if item != value
            ]
        elif kind == "audience":
            preferences["audience_intents"] = [
                item for item in preferences.get("audience_intents", []) if item != value
            ]
            preferences.get("audience_certification_limits", {}).pop(value, None)
            mapping = load_query_rules()["audience_occasions"].get(value, {})
            for genre in mapping.get("genres", []):
                preferences["genres"] = [
                    item for item in preferences.get("genres", []) if item != genre
                ]
        elif kind == "negation":
            preferences["unclear_negation"] = False
        elif kind == "ambiguous":
            surface, _, category = value.partition(":")
            removed = [
                item for item in preferences.get("ambiguous_interpretations", [])
                if item.get("surface") == surface
                and (not category or item.get("category") == category)
            ]
            preferences["ambiguous_interpretations"] = [
                item for item in preferences.get("ambiguous_interpretations", [])
                if item not in removed
            ]
            preferences["soft_exclusions"] = [
                item for item in preferences.get("soft_exclusions", [])
                if not any(item == f"{match['category']}:{match['tag']}" for match in removed)
            ]
            if understanding:
                understanding["ambiguous_interpretations"] = [
                    item for item in understanding.get("ambiguous_interpretations", [])
                    if not any(
                        item.get("surface") == match["surface"]
                        and item.get("category") == match["category"]
                        for match in removed
                    )
                ]
                removed_tags = {item["tag"] for item in removed}
                understanding["keywords"] = [
                    item for item in understanding.get("keywords", [])
                    if item not in removed_tags
                ]
                understanding["positive_requirements"] = [
                    item for item in understanding.get("positive_requirements", [])
                    if item not in removed_tags
                ]
                removed_terms = {
                    value
                    for item in removed
                    for value in (item["surface"], item["tag"])
                }
                understanding["negative_requirements"] = [
                    item for item in understanding.get("negative_requirements", [])
                    if item not in removed_terms
                ]


def movie_matches_runtime_limits(movie, preferences):
    """True only when available runtime data satisfies an explicit limit."""
    runtime = (preferences or {}).get("runtime") or {}
    return evaluate_runtime_status(movie, runtime) == "satisfied"


def evaluate_audience_certification_status(movie, preferences):
    limits = list((preferences or {}).get("audience_certification_limits", {}).values())
    limits = [limit for limit in limits if limit]
    if not limits:
        return "satisfied"
    certification = next((
        movie.get(key) for key in ("certification", "content_rating", "rating_certificate")
        if movie.get(key)
    ), None)
    if not certification:
        return "unverifiable"
    scale = {"G": 0, "PG": 1, "PG-13": 2, "R": 3, "NC-17": 4}
    value = normalize_title(certification).replace(" ", "-").upper()
    value = {"PG13": "PG-13", "NC17": "NC-17"}.get(value, value)
    if value not in scale:
        return "unverifiable"
    requested = []
    for limit in limits:
        normalized = normalize_title(limit).replace(" ", "-").upper()
        normalized = {"PG13": "PG-13", "NC17": "NC-17"}.get(normalized, normalized)
        if normalized not in scale:
            return "unverifiable"
        requested.append(scale[normalized])
    return "satisfied" if all(
        scale[value] <= limit for limit in requested
    ) else "violated"


def movie_matches_audience_certification(movie, preferences):
    return evaluate_audience_certification_status(movie, preferences) == "satisfied"


def describe_tag(tag):
    """Plain-language phrase for a removable tag, e.g. 'under 100 min' or '1990s'."""
    label, value = tag["label"], tag["value"]
    if label == "Exclude":
        return f"excluding {value.lower()}"
    if label == "Runtime":
        if value.startswith("<"):
            return f"under {value[1:].strip()}"
        if value.startswith(">"):
            return f"over {value[1:].strip()}"
        if value.startswith("~"):
            return f"about {value[1:].strip()}"
        return value
    if label == "Year":
        span = re.fullmatch(r"(\d{4})-(\d{4})", value)
        if span and int(span.group(1)) % 10 == 0 and int(span.group(2)) == int(span.group(1)) + 9:
            return f"{span.group(1)}s"
        return f"from {value}" if span else value
    return value.lower()


def build_preference_tags(preferences, excluded_count):
    """Turns the preference dict into a list of small display tags for the template."""
    tags = []

    for g in preferences["genres"]:
        tags.append({"type": "include", "label": "Genre", "value": title_case(g), "key": f"genre:{g}"})
    for m in preferences["moods"]:
        tags.append({"type": "include", "label": "Mood", "value": title_case(m), "key": f"mood:{m}"})
    for t in preferences["themes"]:
        tags.append({"type": "include", "label": "Theme", "value": title_case(t), "key": f"theme:{t}"})
    for g in preferences["excluded_genres"]:
        tags.append({"type": "exclude", "label": "Exclude", "value": title_case(g), "key": f"exclude_genre:{g}"})
    for m in preferences["excluded_moods"]:
        tags.append({"type": "exclude", "label": "Exclude", "value": title_case(m), "key": f"exclude_mood:{m}"})
    for t in preferences["excluded_themes"]:
        tags.append({"type": "exclude", "label": "Exclude", "value": title_case(t), "key": f"exclude_theme:{t}"})
    for descriptor in preferences.get("excluded_content_descriptors", []):
        tags.append({
            "type": "exclude", "label": "Exclude", "value": title_case(descriptor),
            "key": f"exclude_content_descriptor:{descriptor}",
        })
    for keyword in preferences.get("excluded_keywords", []):
        tags.append({
            "type": "exclude", "label": "Exclude", "value": title_case(keyword),
            "key": f"exclude_keyword:{keyword}",
        })
    for exclusion in preferences.get("soft_exclusions", []):
        _, _, value = exclusion.partition(":")
        tags.append({
            "type": "exclude",
            "label": "mild only",
            "value": title_case(value),
            "key": f"soft_exclusion:{exclusion}",
        })
    for intent in preferences.get("audience_intents", []):
        tags.append({
            "type": "neutral",
            "label": "Audience",
            "value": title_case(intent),
            "key": f"audience:{intent}",
        })
    for interpretation in preferences.get("ambiguous_interpretations", []):
        tags.append({
            "type": "neutral",
            "label": "Interpreted",
            "value": f" '{interpretation['surface']}' as {interpretation['category']}",
            "key": (
                f"ambiguous:{interpretation['surface']}:"
                f"{interpretation['category']}"
            ),
        })
    if preferences.get("unclear_negation"):
        tags.append({
            "type": "exclude",
            "label": "Clarify",
            "value": "I wasn't sure what to exclude",
            "key": "negation:unclear",
        })

    rt = preferences["runtime"]
    if rt["max"] is not None and rt["min"] is not None and rt["min"] > rt["max"]:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"> {rt['min']} min", "key": "runtime_min"})
        tags.append({"type": "neutral", "label": "Runtime", "value": f"< {rt['max']} min", "key": "runtime_max"})
    elif rt["max"] is not None and rt["min"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"{rt['min']}-{rt['max']} min", "key": "runtime"})
    elif rt["max"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"< {rt['max']} min", "key": "runtime"})
    elif rt["min"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"> {rt['min']} min", "key": "runtime"})
    elif rt["target"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"~ {rt['target']} min", "key": "runtime"})

    ry = preferences["release_year"]
    if ry:
        if ry["min"] and ry["max"] and ry["min"] > ry["max"]:
            tags.append({"type": "neutral", "label": "Year", "value": f"after {ry['min']}", "key": "year_min"})
            tags.append({"type": "neutral", "label": "Year", "value": f"before {ry['max']}", "key": "year_max"})
        elif ry["min"] and ry["max"]:
            tags.append({"type": "neutral", "label": "Year", "value": f"{ry['min']}-{ry['max']}", "key": "year"})
        elif ry["min"]:
            tags.append({"type": "neutral", "label": "Year", "value": f"after {ry['min']}", "key": "year"})
        elif ry["max"]:
            tags.append({"type": "neutral", "label": "Year", "value": f"before {ry['max']}", "key": "year"})

    if preferences["free_text_keywords"]:
        tags.append({
            "type": "neutral",
            "label": "Keywords",
            "value": ", ".join(preferences["free_text_keywords"][:6]),
        })

    if excluded_count > 0:
        noun = "movie" if excluded_count == 1 else "movies"
        tags.append({"type": "exclude", "label": "Filtered out", "value": f"{excluded_count} {noun}"})

    return tags


def _renderable_preference_tags(preferences, excluded_count, dropped):
    tags = build_preference_tags(preferences, excluded_count)
    if not has_request_context():
        return tags
    kept_args = {}
    for key in (
        "q", "genre", "nationality", "awards", "per_page", "page", "debug",
        "remove_genre", "remove_awards", "drop",
    ):
        values = request.args.getlist(key)[:12]
        if values:
            kept_args[key] = [value[:MAX_QUERY_LENGTH] for value in values]
    return [
        {
            **tag,
            "phrase": describe_tag(tag),
            "remove_url": url_for("index", **{
                **kept_args,
                "drop": (dropped + [tag["key"]])[:32],
                "page": 1,
            }),
        } if "key" in tag else tag
        for tag in tags
    ]


def _append_alternative_preference_tags(context, alternatives, dropped):
    tags = context.setdefault("preference_tags", [])
    existing_keys = {tag.get("key") for tag in tags}
    for tag in alternatives:
        key = tag.get("key")
        if key in dropped or key in existing_keys:
            continue
        retained = {
            name: request.args.getlist(name)[:12]
            for name in ("q", "genre", "nationality", "awards", "per_page", "page", "debug", "drop")
            if request.args.getlist(name)
        }
        tags.append({
            **tag,
            "phrase": tag["value"],
            "remove_url": url_for(
                "index",
                **{**retained, "drop": (dropped + [key])[:32], "page": 1},
            ),
        })
        existing_keys.add(key)


def _result_tier_counts(results):
    counts = {"exact": 0, "close": 0, "related": 0}
    for result in results:
        tier = result.get("match_tier", "close")
        counts[tier if tier in counts else "close"] += 1
    return counts


def _topic_support_index():
    global _TOPIC_SUPPORT_SOURCE, _TOPIC_SUPPORT
    if _TOPIC_SUPPORT_SOURCE is MOVIES:
        return _TOPIC_SUPPORT
    support = Counter()
    vocabulary = set()
    for movie in MOVIES:
        phrases = set()
        title_values = {
            normalize_phrase(value)
            for value in (movie.get("title", ""), movie.get("original_title", ""))
            if value
        }
        for field in ("synopsis", "overview", "keywords", "themes", "genres"):
            values = movie.get(field, []) or []
            values = [values] if isinstance(values, str) else values
            for value in values:
                if field == "synopsis" and _GENERATED_SYNOPSIS_RE.match(value.strip()):
                    continue
                if normalize_phrase(value) in title_values:
                    continue
                tokens = normalize_phrase(value).split()
                for size in range(1, min(3, len(tokens)) + 1):
                    phrases.update(
                        " ".join(tokens[start:start + size])
                        for start in range(len(tokens) - size + 1)
                    )
        support.update(phrases)
        vocabulary.update(phrase for phrase in phrases if " " not in phrase)
    _TOPIC_SUPPORT_SOURCE = MOVIES
    _TOPIC_SUPPORT = (support, vocabulary)
    return _TOPIC_SUPPORT


def _scarce_topic_notice(query, understanding):
    phrases = set(understanding.get("required_terms") or [])
    phrases.update(understanding.get("themes") or [])
    phrases.update(
        phrase for phrase in (understanding.get("keywords") or [])
        if len(normalize_phrase(phrase).split()) > 1
    )
    query_tokens = [
        token for token in normalize_phrase(query).split()
        if token not in STOP_WORDS and not token.isdigit()
    ][:12]
    for size in (1, 2, 3):
        phrases.update(
            " ".join(query_tokens[start:start + size])
            for start in range(len(query_tokens) - size + 1)
        )
    phrase_support, vocabulary = _topic_support_index()

    def direct_support(phrase):
        return phrase_support.get(normalize_phrase(phrase), 0)

    for phrase in tuple(phrases):
        normalized = normalize_phrase(phrase)
        if not normalized or " " in normalized or direct_support(normalized):
            continue
        splits = []
        for offset in range(3, len(normalized) - 2):
            left, right = normalized[:offset], normalized[offset:]
            if left in vocabulary and right in vocabulary:
                joined = f"{left} {right}"
                splits.append((
                    direct_support(joined),
                    phrase_support[left] + phrase_support[right],
                    joined,
                ))
        if splits:
            phrases.add(max(splits, key=lambda item: (item[0], item[1], item[2]))[2])

    sparse = {}
    for phrase in phrases:
        normalized = normalize_phrase(phrase)
        support = direct_support(normalized)
        display_phrase = normalized
        if not support and " " not in normalized:
            splits = []
            for offset in range(3, len(normalized) - 2):
                left, right = normalized[:offset], normalized[offset:]
                if left in vocabulary and right in vocabulary:
                    joined = f"{left} {right}"
                    splits.append((direct_support(joined), joined))
            if splits:
                support, display_phrase = max(splits, key=lambda item: (item[0], item[1]))
        if 1 <= support <= 5:
            sparse[display_phrase] = support
    if not sparse:
        return ""
    for phrase in tuple(sparse):
        tokens = phrase.split()
        if any(
            len(other.split()) > len(tokens)
            and all(token in other.split() for token in tokens)
            for other in sparse
        ):
            sparse.pop(phrase)
    details = ", ".join(
        f'"{phrase}" ({support} {"movie" if support == 1 else "movies"})'
        for phrase, support in sorted(sparse.items())
    )
    return f"The catalog has direct metadata evidence for only {details}."


@app.route("/", methods=["GET"])
@_resilient_search
def index():
    raw_query = request.args.get("q", "")
    query_was_capped = len(raw_query) > MAX_QUERY_LENGTH
    query = raw_query[:MAX_QUERY_LENGTH].strip()
    exact_search = request.args.get("exact") == "1"
    query_notices = [
        f"Search was limited to the first {MAX_QUERY_LENGTH} characters."
        if query_was_capped else "",
        INDEX_FALLBACK_NOTICE,
    ]
    query_notice = " ".join(part for part in query_notices if part)
    coverage_notices = []
    trace = getattr(g, "search_trace", None)
    if trace is not None:
        trace.count("input", 1 if query else 0)
    _run_search_stage(
        "normalize",
        lambda: normalize_text(query),
        query.casefold(),
        trace,
    )
    parsed_query_preferences = extract_preferences(query) if query else None
    title_search_query = (
        remove_negated_terms(query, parsed_query_preferences) if query else ""
    )
    if query:
        raw_title_matches = _exact_title_query_candidates(query)
        if raw_title_matches and is_exact_title_query(query, raw_title_matches):
            title_search_query = query
    if query and not allow_search(request.remote_addr or "unknown"):
        return "Search limit reached. Please wait a minute and try again.", 429
    normal_search_args = set(request.args.keys()) == {"q"}
    html_cache_key = None
    if query and normal_search_args:
        cache_polarity = tuple(sorted(
            (polarity, field, value)
            for polarity, field in (
                ("include", "genres"),
                ("include", "moods"),
                ("include", "themes"),
                ("exclude", "excluded_genres"),
                ("exclude", "excluded_moods"),
                ("exclude", "excluded_themes"),
                ("exclude", "excluded_content_descriptors"),
                ("exclude", "excluded_keywords"),
            )
            for value in (
                parsed_query_preferences.get(field, [])
                if parsed_query_preferences else []
            )
        ))
        html_cache_key = (
            CATALOG_VERSION["record_count"],
            CATALOG_VERSION["content_hash"],
            normalize_text(query).casefold(),
            cache_polarity,
        )
        cached_html = _NORMAL_SEARCH_HTML_CACHE.get(html_cache_key)
        if cached_html is not None:
            return make_response(cached_html)
    like_source_text = find_like_source_text(query) if query else None
    like_source_movie, like_title_suggestions = (
        resolve_like_title(query, MOVIES) if like_source_text else (None, [])
    )
    debug_enabled = request.args.get("debug", "")[:16].strip().lower() in {"1", "true", "yes"}
    selected_genres = list(dict.fromkeys(
        _canonical_genre(value[:64].strip().lower())
        for value in request.args.getlist("genre")[:12] if value[:64].strip()
    ))
    removed_genres = {
        value[:64].strip().lower() for value in request.args.getlist("remove_genre")[:12]
    }
    selected_genres = [
        genre for genre in selected_genres
        if genre not in removed_genres
    ]
    selected_genre = selected_genres[0] if selected_genres else ""
    selected_nationality = request.args.get("nationality", "")[:16].strip().upper()
    selected_awards = list(dict.fromkeys(
        value[:64].strip().lower() for value in request.args.getlist("awards")[:12]
        if value[:64].strip().lower() in RECOGNITION_OPTIONS
    ))
    removed_awards = {
        value[:64].strip().lower() for value in request.args.getlist("remove_awards")[:12]
    }
    selected_awards = [
        award for award in selected_awards
        if award not in removed_awards
    ]
    award_filter = bool(selected_awards)
    filter_conflicts = []
    query_title_matches = _run_search_stage(
        "title-match",
        lambda: find_title_matches(title_search_query, MOVIES),
        [],
        trace,
    ) if query and not like_source_text else []
    title_prefix_query = False
    title_spans = _filter_entity_title_spans(title_search_query, detect_title_spans(
        title_search_query, MOVIES, TITLE_STOP_WORDS, RISKY_TITLE_VOCABULARY,
    )) if query and not like_source_text else []
    embedded_title_intent = bool(title_spans and not title_spans[0]["whole_query"])
    title_residual = (
        "" if like_source_text else
        remove_title_text(query, query_title_matches) if query_title_matches else query
    )
    initial_query = _run_search_stage(
        "parse",
        lambda: build_structured_query(
            title_residual,
            allow_fuzzy_correction=not exact_search,
        ),
        None,
        trace,
    ) if query else None
    if query and initial_query is None:
        initial_query = _minimal_query_analysis(title_residual, trace)
    if trace is not None:
        trace.count("title-matches", len(query_title_matches))
    if query_title_matches:
        normalized_title_query = _title_key(title_search_query)
        title_query_without_article = re.sub(
            r"^(?:the|a|an)\s+", "", normalized_title_query,
        )
        title_prefix_query = not is_exact_title_query(title_search_query, query_title_matches) and any(
            _title_key(movie["title"]).startswith(normalized_title_query)
            or _title_key(movie["title"]).startswith(title_query_without_article)
            for movie in query_title_matches
        )
        preference_query = _run_search_stage(
            "parse",
            lambda: "" if title_prefix_query else remove_title_text(query, query_title_matches),
            query,
            trace,
        )
        query_analysis = _run_search_stage(
            "parse",
            lambda: build_structured_query(
                preference_query,
                allow_fuzzy_correction=not exact_search,
            ),
            None,
            trace,
        )
        if query_analysis is None:
            query_analysis = _minimal_query_analysis(preference_query, trace)
        detected_nationality = query_analysis["country"]
        nationality_query = preference_query
    else:
        detected_nationality = initial_query["country"] if initial_query else ""
        nationality_query = initial_query["cleaned_text"] if initial_query else query
        query_title_matches = _run_search_stage(
            "title-match",
            lambda: find_title_matches(
                remove_negated_terms(nationality_query), MOVIES,
            ),
            [],
            trace,
        ) if query else []
        preference_query = _run_search_stage(
            "parse",
            lambda: remove_title_text(nationality_query, query_title_matches),
            query,
            trace,
        )
        query_analysis = _run_search_stage(
            "parse",
            lambda: build_structured_query(
                query,
                preference_query,
                allow_fuzzy_correction=not exact_search,
            ),
            None,
            trace,
        ) if query else None
        if query and query_analysis is None:
            query_analysis = _minimal_query_analysis(preference_query, trace)
    if trace is not None:
        trace.count("title-matches", len(query_title_matches))
    if selected_nationality and detected_nationality and selected_nationality != detected_nationality:
        filter_conflicts.append("The country in your search overrides the country menu selection.")
    effective_nationality = detected_nationality or selected_nationality
    query_preferences = query_analysis["preferences"] if query_analysis else None
    selected_ambiguous_exclusions = []
    if query_preferences and selected_genres:
        for interpretation in query_preferences.get("ambiguous_interpretations", []):
            tag = interpretation.get("tag")
            if (
                interpretation.get("negated")
                and interpretation.get("category") == "genre"
                and tag in selected_genres
            ):
                if tag not in query_preferences["excluded_genres"]:
                    query_preferences["excluded_genres"].append(tag)
                selected_ambiguous_exclusions.append(interpretation)
        if selected_ambiguous_exclusions:
            query_preferences["ambiguous_interpretations"] = [
                item for item in query_preferences["ambiguous_interpretations"]
                if item not in selected_ambiguous_exclusions
            ]
            removed_soft_tags = {
                f"{item['category']}:{item['tag']}"
                for item in selected_ambiguous_exclusions
            }
            query_preferences["soft_exclusions"] = [
                item for item in query_preferences.get("soft_exclusions", [])
                if item not in removed_soft_tags
            ]
    if query_preferences and query_preferences.get("audience_certification_limits"):
        if CATALOG_FIELD_COVERAGE["certification"] < max(10, len(MOVIES) * 0.1):
            coverage_notices.append(
                "The catalog has certification data for only "
                f"{CATALOG_FIELD_COVERAGE['certification']} of {len(MOVIES)} movies, "
                "so age-rating limits cannot be checked reliably."
            )
    if query_preferences and query_preferences["genres"]:
        if selected_genres and not any(genre in query_preferences["genres"] for genre in selected_genres):
            filter_conflicts.append("The selected genre filter overrides the genre in your search.")
    specific_title_matches = [
        movie for movie in query_title_matches
        if normalize_text(movie["title"]).strip() not in {"movie", "movies", "film", "films"}
    ]
    title_only_intent = specific_title_matches and not any((
        query_preferences["genres"], query_preferences["moods"], query_preferences["themes"],
        query_preferences["free_text_keywords"], query_preferences["runtime"]["min"],
        query_preferences["runtime"]["max"], query_preferences["runtime"]["target"],
        query_preferences["release_year"],
    )) and not detected_nationality
    if title_only_intent:
        if not selected_genre:
            effective_nationality = ""
    understanding = _run_search_stage(
        "parse",
        lambda: copy.deepcopy(interpret_query(query, exact_search=exact_search)),
        None,
        trace,
    ) if query else None
    if query and understanding is None:
        understanding = _minimal_understanding(query, query_analysis)
    if query_title_matches:
        raw_understanding = understanding
        understanding = _run_search_stage(
            "parse",
            lambda: copy.deepcopy(interpret_query(
                preference_query,
                exact_search=exact_search,
            )),
            None,
            trace,
        )
        if understanding is None:
            understanding = _minimal_understanding(preference_query, query_analysis)
        if raw_understanding and raw_understanding.get("similar_to"):
            understanding["similar_to"] = raw_understanding["similar_to"]
    if understanding and selected_ambiguous_exclusions:
        resolved_surfaces = {
            item["surface"] for item in selected_ambiguous_exclusions
        }
        understanding["ambiguous_interpretations"] = [
            item for item in understanding.get("ambiguous_interpretations", [])
            if item.get("surface") not in resolved_surfaces
        ]
    if like_source_movie and understanding:
        understanding["similar_to"] = like_source_movie["title"]
        understanding["similar_to_movie_id"] = like_source_movie["id"]
    like_series_members = []
    if like_source_movie:
        like_series = next((
            group for group in SERIES_GROUPS
            if like_source_movie.get("title") in group["titles"]
        ), None)
        if like_series:
            like_series_members = [
                movie for movie in MOVIES
                if movie.get("title") in like_series["titles"]
                and movie.get("id") != like_source_movie.get("id")
            ]
    alternate_interpretations = []
    if embedded_title_intent and query_preferences:
        embedded_query_analysis = _run_search_stage(
            "parse",
            lambda: build_structured_query(
                query,
                allow_fuzzy_correction=not exact_search,
            ),
            None,
            trace,
        )
        embedded_preferences = (
            embedded_query_analysis["preferences"] if embedded_query_analysis else {}
        )
        for field, label in (("genres", "genre"), ("moods", "mood"), ("themes", "theme")):
            for value in embedded_preferences.get(field, []):
                if value in query_preferences.get(field, []):
                    continue
                alternate_interpretations.append({
                    "type": "neutral",
                    "label": "Did you mean",
                    "value": f"{title_case(value)} as a {label}?",
                    "key": f"alternate:{label}:{value}",
                })
        for interpretation in embedded_preferences.get("ambiguous_interpretations", []):
            label = interpretation["category"].replace("_", " ")
            alternate_interpretations.append({
                "type": "neutral",
                "label": "Did you mean",
                "value": (
                    f"{title_case(interpretation['surface'])} as a {label}?"
                ),
                "key": (
                    f"alternate:{label}:{interpretation['surface']}"
                ),
            })
    bare_year_title = re.fullmatch(r"\s*((?:18|19|20)\d{2})\s*(?:movies?|films?)?\s*", query, re.IGNORECASE)
    if bare_year_title and query_title_matches:
        year_value = bare_year_title.group(1)
        alternate_interpretations.append({
            "type": "neutral",
            "label": "Did you mean",
            "value": f"{year_value} as a year?",
            "key": f"alternate:year:{year_value}",
        })
    try:
        per_page = int(request.args.get("per_page", str(DEFAULT_PER_PAGE))[:16])
    except ValueError:
        per_page = DEFAULT_PER_PAGE
    if per_page not in PER_PAGE_OPTIONS:
        per_page = DEFAULT_PER_PAGE
    try:
        page_number = max(1, int(request.args.get("page", "1")[:16]))
    except ValueError:
        page_number = 1
    genres = sorted({_canonical_genre(genre) for movie in MOVIES for genre in movie.get("genres", [])})
    nationalities = sorted({
        code for movie in MOVIES for code in movie_country_codes(movie) if code
    })
    llm_year_range = understanding["year_range"] if understanding else {}
    llm_min_rating = understanding["min_rating"] if understanding else None
    query_genres = [] if selected_genre else (list(query_preferences["genres"]) if query_preferences else [])
    if selected_genre and query_preferences:
        query_preferences["genres"] = list(selected_genres)
    if query_preferences and understanding:
        if not query_genres and not selected_genre:
            query_genres = [genre for genre in understanding["genres"] if genre in genres]
            query_preferences["genres"] = query_genres
        if not query_preferences["release_year"] and (llm_year_range.get("from") or llm_year_range.get("to")):
            query_preferences["release_year"] = {
                "min": llm_year_range.get("from"),
                "max": llm_year_range.get("to"),
            }
        for exclusion in understanding["exclusions"]:
            excluded_genre = normalize_text(exclusion).replace(",", " ").strip()
            if excluded_genre in genres and excluded_genre not in query_preferences["excluded_genres"]:
                query_preferences["excluded_genres"].append(excluded_genre)
        query_genres = [
            genre for genre in query_genres
            if genre not in query_preferences["excluded_genres"]
        ]

    dropped = [value[:96] for value in request.args.getlist("drop")[:32]]
    if dropped and query_preferences:
        drop_constraints(query_preferences, understanding, dropped)
        dropped_genres = {d.partition(":")[2] for d in dropped if d.startswith("genre:")}
        query_genres = [genre for genre in query_genres if genre not in dropped_genres]
        if any(value.startswith("audience:") for value in dropped):
            query_genres = [
                genre for genre in query_preferences.get("genres", [])
                if genre not in query_preferences.get("excluded_genres", [])
            ]
        llm_year_range = understanding["year_range"]

    if query_preferences:
        filter_conflicts.extend(
            conflict for conflict in find_filter_conflicts(query_preferences, selected_genres)
            if conflict not in filter_conflicts
        )
    year_constraint = query_preferences["release_year"] if query_preferences else None
    active_entity_index = (
        ENTITY_INDEX if MOVIES is _CATALOG_SOURCE else build_entity_index(MOVIES)
    )
    country_ids = active_entity_index["country"].get(effective_nationality) if effective_nationality else None
    award_ids = (
        set().union(*(active_entity_index["award"].get(key, set()) for key in selected_awards))
        if selected_awards else None
    )
    entity_ids = None
    if query_preferences and query_preferences.get("entity_requests"):
        entity_ids = None
        for entity_request in query_preferences["entity_requests"]:
            field = entity_request["field"]
            entity_key = normalize_title(entity_request["value"])
            request_ids = active_entity_index.get(field, {}).get(entity_key, set())
            entity_ids = set(request_ids) if entity_ids is None else entity_ids.intersection(request_ids)

    def matching_base_filters(movie):
        movie_genres = movie.get("genres") or []
        return (
            (
                not selected_genres or not movie_genres
                or any(genre in movie_genres for genre in selected_genres)
            )
            and (not effective_nationality or str(movie.get("id")) in (country_ids or set()))
            and (not selected_awards or str(movie.get("id")) in (award_ids or set()))
            and (entity_ids is None or str(movie.get("id")) in entity_ids)
        )

    excluded_direct_ids = _excluded_genre_movie_ids(query_preferences, MOVIES)
    base_movies = [
        movie for movie in MOVIES
        if str(movie.get("id", "")) not in excluded_direct_ids
        and matching_base_filters(movie)
    ]
    if trace is not None:
        trace.count("base-filtered", len(base_movies))

    filter_status_by_movie_id = {}

    def search_pool():
        pool = []
        filter_status_by_movie_id.clear()
        for movie in base_movies:
            active_genres = list(dict.fromkeys([*selected_genres, *query_genres]))
            active_genres = [
                genre for genre in active_genres
                if genre not in (query_preferences.get("excluded_genres", []) if query_preferences else [])
            ]
            constraints = evaluate_movie_constraints(
                movie, query_preferences, active_genres,
            )
            if constraints["status"] == "violated":
                continue
            audience_status = evaluate_audience_certification_status(
                movie, query_preferences,
            )
            if audience_status == "violated":
                continue
            checks = list(constraints["checks"])
            if query_preferences and query_preferences.get("audience_certification_limits"):
                checks.append({
                    "constraint": "certification",
                    "status": audience_status,
                })
            if llm_min_rating is not None:
                rating = movie.get("average_rating")
                rating_status = (
                    "unverifiable" if rating is None
                    else "satisfied" if rating >= llm_min_rating
                    else "violated"
                )
                if rating_status == "violated":
                    continue
                checks.append({
                    "constraint": "rating",
                    "status": rating_status,
                })
            if query_preferences and query_preferences.get("cast_gender") and not evaluate_cast_constraint(movie, query_preferences["cast_gender"]):
                continue
            filter_status_by_movie_id[str(movie.get("id"))] = checks
            pool.append(movie)
        return pool

    filtered_movies = _run_search_stage(
        "filter", search_pool, [], trace, complete_after_budget=True,
    )
    if trace is not None:
        trace.count("filtered", len(filtered_movies))
    search_notice = " ".join(part for part in (query_notice, *coverage_notices) if part)
    normalization_notice = ""
    exact_search_url = ""

    context = {
        "query": query,
        "enforced_exclusion_keys": [
            f"{prefix}:{value}"
            for field, prefix in (
                ("excluded_genres", "exclude_genre"),
                ("excluded_moods", "exclude_mood"),
                ("excluded_themes", "exclude_theme"),
                ("excluded_content_descriptors", "exclude_content_descriptor"),
                ("excluded_keywords", "exclude_keyword"),
            )
            for value in (query_preferences.get(field, []) if query_preferences else [])
        ],
        "dropped": dropped,
        "feedback_saved": request.args.get("feedback") == "saved",
        "selected_genre": selected_genre,
        "selected_genres": selected_genres,
        "selected_awards": selected_awards,
        "recognition_options": RECOGNITION_OPTIONS,
        "selected_nationality": effective_nationality,
        "award_filter": award_filter,
        "search_notice": search_notice,
        "alternative_queries": understanding["alternative_queries"] if understanding else [],
        "assistant_message": "",
        "assistant_state": "",
        "detected_nationality": detected_nationality,
        "filter_conflicts": filter_conflicts,
        "similar_to_title": like_source_movie.get("title") if like_source_movie else "",
        "similar_series_members": like_series_members,
        "like_title_suggestions": like_title_suggestions,
        "nationality_label": COUNTRY_LABELS.get(effective_nationality, effective_nationality),
        "genres": genres,
        "nationalities": nationalities,
        "nationality_labels": COUNTRY_LABELS,
        "movie_origin_label": movie_origin_label,
        "movie_poster_url": movie_poster_url,
        "per_page": per_page,
        "per_page_options": PER_PAGE_OPTIONS,
        "page_number": page_number,
        "total_pages": 1,
        "examples": EXAMPLE_QUERIES,
        "preference_tags": _run_search_stage(
            "render-prep",
            lambda: _renderable_preference_tags(
                query_preferences, 0, dropped,
            ) if query_preferences else [],
            [],
            trace,
        ),
        "results": [],
        "movies": [],
        "searched": False,
        "result_count": len(filtered_movies),
        "result_count_label": "",
        "result_tier_counts": {"exact": 0, "close": 0, "related": 0},
        "query_debug": query_analysis["debug"] if query_analysis else {},
        "debug_enabled": debug_enabled,
        "debug_view": None,
    }
    _append_alternative_preference_tags(context, alternate_interpretations, dropped)
    g.search_context = context

    if not query:
        context["assistant_message"] = build_assistant_message("", len(filtered_movies))

    if query:
        if like_source_text and not like_source_movie:
            context.update({
                "searched": True,
                "result_count": 0,
                "result_count_label": "0 exact, 0 close, 0 related",
                "results": [],
                "assistant_message": f"I couldn't find a catalog title matching “{like_source_text}”. Try one of the nearby titles below.",
                "assistant_state": "empty",
                "search_notice": "The source title was not resolved, so I did not fall back to a text search.",
            })
            if trace is not None:
                trace.stage = "render"
            rendered = render_template("index.html", **context)
            if (
                html_cache_key is not None
                and "may be incomplete" not in str(context.get("search_notice", "")).casefold()
                and "partial" not in str(context.get("search_notice", "")).casefold()
            ):
                if len(_NORMAL_SEARCH_HTML_CACHE) >= NORMAL_SEARCH_HTML_CACHE_SIZE:
                    oldest_key = next(iter(_NORMAL_SEARCH_HTML_CACHE))
                    del _NORMAL_SEARCH_HTML_CACHE[oldest_key]
                _NORMAL_SEARCH_HTML_CACHE[html_cache_key] = rendered
            return rendered
        unsupported = query_analysis["unsupported_filters"]
        unsupported_requests = []
        requested_fields = []
        if unsupported["people"]:
            if re.search(r"\bstarring\b|\bwith\b", query_analysis["normalized_query"]):
                requested_fields.append("cast")
            if re.search(r"\bdirected by\b", query_analysis["normalized_query"]):
                requested_fields.append("director")
            if re.search(r"\bwritten by\b", query_analysis["normalized_query"]):
                requested_fields.append("writer")
            if not requested_fields:
                requested_fields.append("cast")
        if unsupported["languages"]:
            requested_fields.append("language")
        if unsupported.get("certifications"):
            requested_fields.append("certification")
        for field in dict.fromkeys(requested_fields):
            coverage = CATALOG_FIELD_COVERAGE.get(field, 0)
            if not coverage:
                label = "cast" if field == "cast" else field
                unsupported_requests.append(
                    f"I can't search by {label} because the catalog has no {label} data. "
                    f"The catalog does not include {label} data."
                )
            elif coverage < max(10, len(MOVIES) * 0.1):
                coverage_notices.append(
                    f"I can't reliably search by {field}: catalog data is available for "
                    f"only {coverage} of {len(MOVIES)} movies."
                )
        if selected_awards or re.search(
            r"\b(?:award|awards|winner|winners|winning)\b",
            query_analysis["normalized_query"],
        ):
            if CATALOG_FIELD_COVERAGE["award"] < max(10, len(MOVIES) * 0.1):
                coverage_notices.append(
                    f"Awards are recorded for only {CATALOG_FIELD_COVERAGE['award']} of {len(MOVIES)} catalog movies."
                )
        if unsupported_requests:
            notice = " ".join(unsupported_requests)
            context.update({
                "searched": True,
                "result_count": 0,
                "result_count_label": "0 exact, 0 close, 0 related",
                "results": [],
                "search_notice": notice,
                "assistant_message": notice,
                "assistant_state": "empty",
                "alternative_queries": _run_search_stage(
                    "explain",
                    lambda: build_alternative_queries(understanding, query_preferences),
                    [],
                    trace,
                ),
            })
            if query_notice:
                context["search_notice"] = " ".join(
                    part for part in (context["search_notice"], query_notice) if part
                )
            for notice in (context["search_notice"], query_notice):
                if notice and notice.casefold() not in context["assistant_message"].casefold():
                    context["assistant_message"] += " " + notice
            if trace is not None:
                trace.stage = "render"
            rendered = render_template("index.html", **context)
            if (
                html_cache_key is not None
                and "may be incomplete" not in str(context.get("search_notice", "")).casefold()
                and "may be incomplete" not in str(context.get("assistant_message", "")).casefold()
            ):
                if len(_NORMAL_SEARCH_HTML_CACHE) >= NORMAL_SEARCH_HTML_CACHE_SIZE:
                    oldest_key = next(iter(_NORMAL_SEARCH_HTML_CACHE))
                    del _NORMAL_SEARCH_HTML_CACHE[oldest_key]
                _NORMAL_SEARCH_HTML_CACHE[html_cache_key] = rendered
            return rendered
        preferences = query_preferences
        cast_constraint = preferences.get("cast_gender")
        negative_cast_constraint = bool(
            cast_constraint
            and (cast_constraint.get("exclude") == "female" or cast_constraint.get("require") == "all_male")
        )
        if has_unsupported_negated_requirement(preference_query) and not query_preferences.get("cast_gender"):
            context.update({
                "searched": True,
                "result_count": 0,
                "result_count_label": "0 exact, 0 close, 0 related",
                "results": [],
                "search_notice": "No close matches found. This catalog cannot verify strict gender-based negatives such as 'no woman', so no movie can be confirmed as a true match.",
                "assistant_message": build_assistant_message(query, 0),
                "assistant_state": "empty",
                "alternative_queries": _run_search_stage(
                    "explain",
                    lambda: build_alternative_queries(understanding, preferences),
                    [],
                    trace,
                ),
            })
            if query_notice:
                context["search_notice"] = " ".join(
                    part for part in (context["search_notice"], query_notice) if part
                )
            for notice in (context["search_notice"], query_notice):
                if notice and notice.casefold() not in context["assistant_message"].casefold():
                    context["assistant_message"] += " " + notice
            if trace is not None:
                trace.stage = "render"
            return render_template("index.html", **context)
        normalized_query = _title_key(remove_negated_terms(nationality_query))
        exact_title_exists = normalized_query in TITLE_INDEX
        nationality_only = detected_nationality and not title_only_intent and not specific_title_matches and not exact_title_exists and not any((
            preferences["genres"], preferences["moods"], preferences["themes"],
            preferences["free_text_keywords"], preferences["runtime"]["min"],
            preferences["runtime"]["max"], preferences["runtime"]["target"],
            preferences["release_year"],
        ))
        if nationality_only:
            title_matches = []
        else:
            title_matches = [movie for movie in query_title_matches if movie in filtered_movies]
        search_movies = title_matches or filtered_movies
        if like_source_movie:
            excluded_titles = {normalize_title(like_source_movie.get("title", ""))}
            series_titles = next((
                group["titles"] for group in SERIES_GROUPS
                if like_source_movie.get("title") in group["titles"]
            ), [])
            excluded_titles.update(normalize_title(title) for title in series_titles)
            search_movies = [
                movie for movie in filtered_movies
                if normalize_title(movie.get("title", "")) not in excluded_titles
            ]
        ranking_preferences = preferences
        if title_matches and is_exact_title_query(title_search_query, title_matches):
            # Words such as "grand" can also be mood synonyms. For an exact
            # title lookup, the title is the user's intent, not a preference.
            ranking_preferences = dict(preferences)
            ranking_preferences["genres"] = []
            ranking_preferences["moods"] = []
            ranking_preferences["themes"] = []
        retrieval_understanding = dict(understanding)
        if title_matches:
            retrieval_understanding["required_terms"] = []
        if retrieval_understanding.get("similar_to"):
            similar_title = find_title_matches(retrieval_understanding["similar_to"], MOVIES)
            if similar_title:
                retrieval_understanding["similar_to_movie_id"] = similar_title[0]["id"]
        if title_matches:
            title_evidence = [
                _contains_complete_title(title_search_query, movie["title"])
                for movie in title_matches
            ]
            candidates = [{
                "movie": movie,
                "search_score": 100,
                "match_reason": f"Matched title: {movie['title']}.",
                "relevance_score": 1.0,
                "query_evidence": {
                    "concepts": [], "requirements": [], "score": 1.0,
                    "strength": 1.0 if title_evidence[index] else 0.35,
                    "strong": title_evidence[index],
                    "details": [{
                        "phrase": movie["title"], "source": "title",
                        "strength": 1.0 if title_evidence[index] else 0.35,
                        "strong": title_evidence[index],
                    }],
                },
                "normalized_terms": [],
                "normalized_query": "",
                "evidence_strength": 1.0 if title_evidence[index] else 0.35,
                "fallback_only": False,
            } for index, movie in enumerate(title_matches)]
        else:
            candidates = _run_search_stage(
                "expand-retrieve",
                lambda: retrieve_candidates(
                    query, retrieval_understanding, search_movies,
                    LIKE_TFIDF_MODEL if retrieval_understanding.get("similar_to") else TFIDF_MODEL,
                ),
                None,
                trace,
            )
            if candidates is None:
                candidates = []
                if trace is not None:
                    trace.fallback("parse-without-expansion")
            if not candidates:
                plain_candidates = _run_search_stage(
                    "plain-bm25-title-tfidf",
                    lambda: retrieve_plain_candidates(
                        query, retrieval_understanding, search_movies, TFIDF_MODEL,
                    ),
                    [],
                    trace,
                )
                if trace is not None:
                    trace.count("plain-retrieved", len(plain_candidates))
                if plain_candidates:
                    candidates = plain_candidates
                    if trace is not None:
                        trace.fallback("plain-bm25-title-tfidf")
                    fallback_notice = "Search used direct catalog matching for the closest available results."
                    search_notice = " ".join(
                        part for part in (search_notice, fallback_notice) if part
                    )
            if trace is not None:
                trace.count("retrieved", len(candidates))
            corrections = dict(
                query_preferences.get("fuzzy_corrections", {})
                if query_preferences else {}
            )
            if understanding:
                corrections.update(understanding.get("fuzzy_corrections", {}))
            for item in candidates:
                corrections.update(item.get("corrections", {}))
            corrected_query = query
            for original, corrected in sorted(
                corrections.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            ):
                corrected_query = re.sub(
                    rf"(?<!\w){re.escape(original)}(?!\w)",
                    corrected,
                    corrected_query,
                    flags=re.IGNORECASE,
                )
            if corrected_query != query:
                correction_notice = (
                    f'Showing results for "{corrected_query}" '
                    f'(you typed "{query}").'
                )
                search_notice = " ".join(
                    part for part in (search_notice, correction_notice) if part
                )
                exact_search_url = url_for(
                    "index",
                    q=query,
                    exact="1",
                    per_page=per_page,
                    genre=selected_genres,
                    nationality=selected_nationality,
                    awards=selected_awards,
                )
            normalized_variant = next((
                item.get("normalized_query") for item in candidates
                if item.get("normalized_query")
            ), "")
            if normalized_variant:
                normalization_notice = (
                    f'Showing results for "{normalized_variant}" '
                    "(a catalog-supported compound variant)."
                )
                search_notice = " ".join(
                    part for part in (search_notice, normalization_notice) if part
                )
        if not candidates and filtered_movies:
            eligible_closest = filtered_movies
            if negative_cast_constraint:
                eligible_closest = [
                    movie for movie in filtered_movies
                    if evaluate_cast_constraint(movie, cast_constraint)
                ]
            closest_movies = heapq.nlargest(MAX_CANDIDATES, eligible_closest, key=movie_rank_key)
            candidates = [{
                "movie": movie,
                "search_score": 0,
                "match_reason": "No direct plot evidence was found; shown as an unverified alternative.",
                "relevance_score": None,
                "fallback_only": True,
            } for movie in closest_movies]
            if trace is not None:
                trace.fallback("top-rated-within-hard-filters")
            fallback_notice = "No lexical matches were found; these are the closest movies that still meet your active filters."
            search_notice = " ".join(
                part for part in (search_notice, fallback_notice) if part
            )
        if trace is not None:
            trace.count("candidates", len(candidates))
        verification_understanding = copy.deepcopy(understanding)
        if title_matches:
            verification_understanding["title_match_ids"] = [
                str(movie["id"]) for movie in title_matches
            ]
        def verify_candidates():
            return rerank_candidates(
                query,
                candidates,
                verification_understanding,
                on_error=trace.error if trace is not None else None,
            )

        candidates = _run_search_stage(
            "verify-rerank",
            verify_candidates,
            None,
            trace,
            complete_after_budget=True,
            fallback_factory=verify_candidates,
        )
        for candidate in candidates:
            checks = filter_status_by_movie_id.get(
                str(candidate.get("movie", {}).get("id")), [],
            )
            unverified_constraints = [
                check["constraint"] for check in checks
                if check["status"] == "unverifiable"
            ]
            candidate["filter_constraint_checks"] = checks
            candidate["filter_unverified"] = bool(unverified_constraints)
            if unverified_constraints:
                candidate["unverifiable"] = list(dict.fromkeys([
                    *candidate.get("unverifiable", []),
                    *unverified_constraints,
                ]))
        if trace is not None:
            trace.count("verified", len(candidates))
        candidate_movies = [item["movie"] for item in candidates]
        scoring_preferences = dict(ranking_preferences)
        normalized_terms = next((
            item.get("normalized_terms") for item in candidates
            if item.get("normalized_terms")
        ), None)
        if normalized_terms and (
            scoring_preferences.get("free_text_keywords")
            or scoring_preferences.get("ambiguous_interpretations")
        ):
            scoring_preferences["free_text_keywords"] = normalized_terms
        evidence_by_id = {
            str(item["movie"]["id"]): item.get("query_evidence")
            for item in candidates if item.get("query_evidence")
        }
        relevance_by_id = {
            str(item["movie"]["id"]): item.get("relevance_score")
            for item in candidates if item.get("relevance_score") is not None
        }
        def score_candidates():
            return rank_movies(
                candidate_movies,
                scoring_preferences,
                TFIDF_MODEL,
                evidence_by_id=evidence_by_id,
                relevance_by_id=relevance_by_id,
                apply_filters=False,
            )

        ranking = _run_search_stage(
            "score",
            score_candidates,
            None,
            trace,
            complete_after_budget=True,
            fallback_factory=score_candidates,
        )
        candidate_by_id = {str(item["movie"]["id"]): item for item in candidates}
        all_results = ranking["results"]
        if trace is not None:
            trace.count("scored", len(all_results))
        title_match_ids = {str(movie["id"]) for movie in title_matches} if title_matches else set()
        title_query_is_exact = bool(
            title_matches and (
                is_exact_title_query(title_search_query, title_matches)
                or embedded_title_intent and not title_prefix_query
            )
        )
        if title_match_ids and title_query_is_exact:
            for entry in all_results:
                if str(entry["movie"]["id"]) in title_match_ids:
                    entry["percent"] = SERIES_MEMBER_MATCH_PERCENT
        for entry in all_results:
            candidate = candidate_by_id.get(str(entry["movie"]["id"]))
            if candidate is None:
                continue
            entry["match_reason"] = candidate["match_reason"]
            entry["satisfied"] = candidate.get("satisfied", [])
            entry["unsatisfied"] = candidate.get("unsatisfied", [])
            entry["unverifiable"] = candidate.get("unverifiable", [])
            entry["filter_unverified"] = candidate.get("filter_unverified", False)
            entry["filter_constraint_checks"] = candidate.get(
                "filter_constraint_checks", [],
            )
            entry["confidence"] = candidate.get("confidence", 0)
            entry["verification_fallback"] = candidate.get("verification_fallback", False)
            candidate["percent"] = entry["percent"]
            candidate["final_score"] = entry["percent"]
            candidate["match_level"] = (
                "full"
                if candidate.get("match_eligible")
                and entry["percent"] >= EXACT_MATCH_THRESHOLD
                else "partial"
            )
            query_evidence = candidate.get("query_evidence", {})
            candidate["match_tier"] = (
                "exact" if candidate["match_level"] == "full"
                else "close" if candidate.get("fallback_only")
                else "related" if query_evidence.get("associated_count", 0) >= 2
                and not query_evidence.get("details")
                else "close"
            )
            entry["match_level"] = candidate["match_level"]
            entry["match_tier"] = candidate["match_tier"]
            entry["evidence_strength"] = candidate.get("evidence_strength", 0.0)
            entry["final_score"] = entry["percent"]
        if title_match_ids and title_query_is_exact:
            for entry in all_results:
                if str(entry["movie"]["id"]) in title_match_ids:
                    entry["percent"] = 100
                    entry["match_level"] = "full"
                    entry["match_tier"] = "exact"
                    entry["match_reason"] = (
                        f"Same series as {title_search_query.strip()}."
                        if len(title_matches) > 1 and not embedded_title_intent
                        else f"Matched title span: {entry['movie']['title']}."
                        if embedded_title_intent
                        else f"Matched title: {entry['movie']['title']}."
                    )
        elif title_prefix_query:
            for entry in all_results:
                if str(entry["movie"]["id"]) in title_match_ids:
                    entry["match_level"] = "partial"
                    entry["match_tier"] = "close"
        safe_results = []
        eligible_ids = {str(movie.get("id")) for movie in filtered_movies}
        for entry in all_results:
            movie = entry["movie"]
            candidate = candidate_by_id.get(str(movie.get("id")), {})
            score = entry.get("percent")
            if (
                str(movie.get("id")) not in eligible_ids
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or score < 0
                or score > 100
                or _final_gate_violations(movie, query_preferences)
            ):
                if trace is not None:
                    trace.error(
                        "final-exclusion-gate",
                        RuntimeError(
                            "Dropped result with invalid score or exclusion violation after "
                            "candidate filtering and scoring"
                        ),
                    )
                continue
            if entry.get("match_level") == "full" and (
                score < EXACT_MATCH_THRESHOLD
                or not candidate.get("match_eligible")
                or candidate.get("unsatisfied")
                or candidate.get("unverifiable")
            ) and str(movie["id"]) not in title_match_ids:
                entry["match_level"] = "partial"
                entry["match_tier"] = "close"
                if trace is not None:
                    trace.error(
                        "self-check",
                        RuntimeError("Downgraded an unverified or below-threshold full result"),
                    )
            safe_results.append(entry)
        all_results = safe_results
        if trace is not None:
            trace.count("self-checked", len(all_results))
            if trace.budget_exceeded:
                trace.report_budget_exceeded("render-prep")
                trace.fallback("partial-render")
        if title_matches:
            title_match_ids = {str(movie["id"]) for movie in title_matches}
            all_results = [
                entry for entry in all_results
                if str(entry["movie"]["id"]) in title_match_ids
            ]
        exact_results = [entry for entry in all_results if entry["match_level"] == "full"]
        if title_matches:
            title_order = {str(movie["id"]): index for index, movie in enumerate(title_matches)}
            all_results.sort(key=lambda entry: (
                title_order.get(str(entry["movie"]["id"]), len(title_order)),
                -(entry["movie"].get("vote_count") or 0),
                str(entry["movie"].get("title", "")).casefold(),
                str(entry["movie"].get("id", "")),
            ))
        elif understanding.get("similar_to"):
            if exact_results:
                all_results = exact_results
            all_results.sort(key=lambda entry: (
                -candidate_by_id[str(entry["movie"]["id"])]["search_score"],
                str(entry["movie"].get("title", "")).casefold(),
                str(entry["movie"].get("id", "")),
            ))
            context["result_count_label"] = f"{len(all_results)} closest matches"
        elif exact_results:
            all_results.sort(key=lambda entry: (
                -len(set(selected_genres).intersection(set(entry["movie"].get("genres", [])))),
                -entry["percent"],
                -entry.get("evidence_strength", 0.0),
                -(entry["movie"].get("average_rating") or 0),
                -(entry["movie"].get("vote_count") or 0),
                str(entry["movie"].get("title", "")).casefold(),
                str(entry["movie"].get("id", "")),
            ))
        else:
            all_results.sort(key=lambda entry: (
                -len(set(selected_genres).intersection(set(entry["movie"].get("genres", [])))),
                -entry["percent"],
                -entry.get("evidence_strength", 0.0),
                -(entry["movie"].get("average_rating") or 0),
                -(entry["movie"].get("vote_count") or 0),
                str(entry["movie"].get("title", "")).casefold(),
                str(entry["movie"].get("id", "")),
            ))
        context["result_tier_counts"] = _result_tier_counts(all_results)
        context["result_count_label"] = ", ".join(
            f'{context["result_tier_counts"][tier]} {tier}'
            for tier in ("exact", "close", "related")
        )
        unverified_count = sum(
            bool(entry.get("filter_unverified")) for entry in all_results
        )
        if unverified_count:
            context["result_count_label"] += f" — {unverified_count} unverified"
        if selected_genres:
            all_count = sum(
                1 for entry in all_results
                if set(selected_genres).issubset(set(entry["movie"].get("genres", [])))
            )
            partial_count = len(all_results) - all_count
            if all_count:
                genre_count_label = f"{all_count} match all selected genres, {partial_count} match some"
            else:
                genre_count_label = f"0 match all selected genres, {partial_count} match some"
            context["result_count_label"] += " — " + genre_count_label
        if query_analysis["ranking_intent"] and not title_matches and not understanding.get("similar_to"):
            all_results.sort(key=lambda entry: (
                str(entry["movie"].get("title", "")).casefold(),
                str(entry["movie"].get("id", "")),
            ))
            all_results.sort(key=lambda entry: (
                ranking_key(entry, query_analysis["ranking_intent"]), entry["final_score"]
            ), reverse=True)
        all_results.sort(key=lambda entry: bool(entry.get("filter_unverified")))
        total_pages = max(1, math.ceil(len(all_results) / per_page))
        page_number = min(page_number, total_pages)
        start = (page_number - 1) * per_page
        results = all_results[start:start + per_page]

        display_results = []
        for rank, entry in enumerate(results, start=1):
            movie = entry["movie"]
            cast_status = _run_search_stage(
                "verify",
                lambda movie=movie: classify_cast_gender(movie),
                {"status": "unknown", "badge": "Unverified", "evidence": [], "reason": ""},
                trace,
            )
            if cast_constraint and not negative_cast_constraint:
                constraint_match = _run_search_stage(
                    "verify",
                    lambda movie=movie: evaluate_cast_constraint(movie, cast_constraint),
                    False,
                    trace,
                )
                cast_status = {
                    **cast_status,
                    "status": "strong" if constraint_match else "unknown",
                    "badge": "Evidence found" if constraint_match else "Unverified",
                    "reason": (
                        "Available top-billed cast or narrative metadata supports this request."
                        if constraint_match else
                        "Available metadata does not verify this cast request."
                    ),
                    "contradicted": False,
                }
            cast_supported = _run_search_stage(
                "verify",
                lambda movie=movie: bool(
                    cast_constraint and not negative_cast_constraint
                    and evaluate_cast_constraint(movie, cast_constraint)
                ),
                False,
                trace,
            )
            reasons = _run_search_stage(
                "explain",
                lambda: build_match_reasons(
                    movie,
                    ranking_preferences,
                    entry.get("breakdown", {}),
                    entry.get("satisfied", []),
                    cast_supported=cast_supported,
                ),
                [{"ok": False, "text": entry.get("match_reason", "Closest available catalog result.")}],
                trace,
            )
            if selected_genres:
                missing = [
                    genre for genre in selected_genres
                    if genre not in movie.get("genres", [])
                ]
                if missing:
                    reasons = list(reasons) + [{
                        "ok": False,
                        "text": "Missing: " + ", ".join(title_case(genre) for genre in missing),
                    }]
            if entry.get("verification_fallback") and not reasons:
                reasons = [{"ok": False, "text": entry["match_reason"]}]
            display_results.append({
                "rank": rank,
                "movie": movie,
                "percent": entry["percent"],
                "breakdown": entry["breakdown"],
                "poster_exists": poster_exists(movie),
                "genres_display": ", ".join(
                    title_case(g) for g in (movie.get("genres") or [])
                ),
                "reasons": reasons,
                "match_reason": entry["match_reason"],
                "match_level": entry["match_level"],
                "match_tier": entry["match_tier"],
                "match_badge": {
                    "exact": "Exact match",
                    "close": "Closest match",
                    "related": "Related match",
                }[entry["match_tier"]],
                "genre_match_count": len(set(selected_genres).intersection(set(movie.get("genres", [])))) ,
                "show_unverified_badge": (
                    bool(
                        entry.get("filter_unverified")
                        or entry["unverifiable"]
                        or entry["verification_fallback"]
                    )
                ),
                "cast_status": cast_status,
                "cast_badge": cast_status["badge"],
                "cast_evidence": cast_status["evidence"],
                "show_cast_evidence": bool(cast_constraint),
            })
        if all_results and not exact_results:
            search_notice = " ".join(part for part in (
                search_notice,
                "We couldn't find a movie that exactly matches your request. Here are the closest matches.",
            ) if part)
            if negative_cast_constraint:
                search_notice += " None of these are confirmed to have no female characters."
            missing = list(dict.fromkeys(
                item for entry in all_results
                for item in entry["unsatisfied"] + entry["unverifiable"]
            ))
            if missing:
                search_notice += " Missing or unverified: " + "; ".join(missing[:3]) + "."
            if normalization_notice:
                search_notice += " " + normalization_notice
        scarce_notice = _run_search_stage(
            "explain",
            lambda: _scarce_topic_notice(query, understanding),
            "",
            trace,
        )
        if scarce_notice:
            search_notice = f"{search_notice} {scarce_notice}".strip()
        if any(entry["verification_fallback"] for entry in all_results):
            fallback_notice = "Some results could not be fully verified against the catalog's metadata."
            search_notice = f"{fallback_notice} {search_notice}" if search_notice else fallback_notice
        missing_requirements = list(dict.fromkeys(
            item for entry in all_results
            for item in entry["unsatisfied"] + entry["unverifiable"]
        ))
        context["assistant_state"] = "exact" if exact_results else "partial" if all_results else "empty"
        context["assistant_message"] = _run_search_stage(
            "explain",
            lambda: build_assistant_message(
                query,
                len(all_results),
                exact_count=len(exact_results),
                genres=(understanding.get("genres") or query_preferences.get("genres", [])),
                understood_terms=understanding.get("keywords", []) or understanding.get("expanded_concepts", []),
                missing_requirements=missing_requirements,
            ),
            "Search completed with the available catalog evidence.",
            trace,
        )
        if normalization_notice:
            context["assistant_message"] += " " + normalization_notice
        if scarce_notice:
            context["assistant_message"] += " " + scarce_notice
        context.update({
            "preference_tags": _run_search_stage(
                "render-prep",
                lambda: _renderable_preference_tags(
                    ranking_preferences, ranking["excluded_count"], dropped,
                ),
                [],
                trace,
            ),
            "results": display_results,
            "searched": True,
            "result_count": len(all_results),
            "page_number": page_number,
            "total_pages": total_pages,
            "debug_view": _run_search_stage(
                "render-prep",
                lambda: build_debug_view(
                    query_analysis, ranking_preferences, len(filtered_movies), len(all_results),
                ),
                None,
                trace,
            ),
            "search_notice": search_notice,
            "exact_search_url": exact_search_url,
            "assistant_message": context["assistant_message"],
            "assistant_state": context["assistant_state"],
        })
        _append_alternative_preference_tags(
            context, alternate_interpretations, dropped,
        )
        if not all_results:
            context["result_tier_counts"] = {"exact": 0, "close": 0, "related": 0}
            context["result_count_label"] = "0 exact, 0 close, 0 related"
            context["alternative_queries"] = _run_search_stage(
                "explain",
                lambda: build_alternative_queries(understanding, query_preferences),
                [],
                trace,
            )
            if "No close matches found" not in search_notice:
                if filtered_movies:
                    empty_notice = (
                        "No close matches could be verified. Your active filters were kept; "
                        "remove a filter or try one of the alternative searches."
                    )
                else:
                    empty_notice = (
                        "No movies satisfy the active filters. Remove one of the removable filters "
                        "or chips to see the closest alternatives."
                    )
                search_notice = (search_notice + " " if search_notice else "") + empty_notice
            context["search_notice"] = search_notice
        expected_page_count = max(
            0,
            min(per_page, len(all_results) - (page_number - 1) * per_page),
        )
        if len(display_results) != expected_page_count:
            if trace is not None:
                trace.error("self-check", RuntimeError("Rendered page count did not match the result count"))
            start = (page_number - 1) * per_page
            results = all_results[start:start + per_page]
            context["results"] = [
                {
                    "rank": start + offset + 1,
                    "movie": entry["movie"],
                    "percent": entry["percent"],
                    "breakdown": entry.get("breakdown", {}),
                    "poster_exists": poster_exists(entry["movie"]),
                    "genres_display": ", ".join(
                        title_case(g) for g in entry["movie"].get("genres", [])
                    ),
                    "reasons": [{"ok": False, "text": entry.get(
                        "match_reason", "Closest available catalog result.",
                    )}],
                    "match_reason": entry.get("match_reason", ""),
                    "match_level": entry.get("match_level", "partial"),
                    "match_tier": entry.get("match_tier", "close"),
                    "match_badge": "Closest match",
                    "show_unverified_badge": True,
                    "cast_status": {"status": "unknown", "badge": "Unverified", "evidence": []},
                    "cast_badge": "Unverified",
                    "cast_evidence": [],
                    "show_cast_evidence": False,
                }
                for offset, entry in enumerate(results)
            ]
        if search_notice:
            message = context.get("assistant_message", "")
            for sentence in search_notice.split(". "):
                sentence = sentence.strip()
                if sentence and sentence.casefold() not in message.casefold():
                    message = f"{message} {sentence}".strip()
            context["assistant_message"] = message
            context["search_notice"] = search_notice
        g.search_context = context
    else:
        total_pages = max(1, math.ceil(len(filtered_movies) / per_page))
        page_number = min(page_number, total_pages)
        start = (page_number - 1) * per_page
        browse_source = sorted(
            filtered_movies,
            key=lambda movie: (
                -len(set(selected_genres).intersection(set(movie.get("genres", [])))) ,
                -(movie.get("average_rating") or 0),
                -(movie.get("vote_count") or 0),
                str(movie.get("title", "")).casefold(),
                str(movie.get("id", "")),
            ),
        )
        context.update({
            "movies": [
                {"movie": movie, "poster_exists": poster_exists(movie)}
                for movie in browse_source[start:start + per_page]
            ],
            "page_number": page_number,
            "total_pages": total_pages,
        })
        if selected_genres:
            all_count = sum(
                1 for movie in browse_source
                if set(selected_genres).issubset(set(movie.get("genres", [])))
            )
            partial_count = len(browse_source) - all_count
            context["result_count"] = len(browse_source)
            context["result_count_label"] = (
                f"{all_count} match all selected genres, {partial_count} match some"
            )

    if trace is not None:
        trace.stage = "render"
        trace.count(
            "rendered",
            len(context.get("results") or context.get("movies") or []),
        )
        if trace.budget_exceeded:
            trace.report_budget_exceeded("render")
            trace.fallback("partial-render")
            partial_notice = (
                "Search exceeded its time budget; the displayed results may be incomplete."
            )
            context["search_notice"] = " ".join(
                part for part in (context.get("search_notice", ""), partial_notice) if part
            )
            if partial_notice.casefold() not in context.get("assistant_message", "").casefold():
                context["assistant_message"] = (
                    context.get("assistant_message", "") + " " + partial_notice
                ).strip()
    rendered = render_template("index.html", **context)
    if (
        html_cache_key is not None
        and "may be incomplete" not in str(context.get("search_notice", "")).casefold()
        and "may be incomplete" not in str(context.get("assistant_message", "")).casefold()
    ):
        if len(_NORMAL_SEARCH_HTML_CACHE) >= NORMAL_SEARCH_HTML_CACHE_SIZE:
            oldest_key = next(iter(_NORMAL_SEARCH_HTML_CACHE))
            del _NORMAL_SEARCH_HTML_CACHE[oldest_key]
        _NORMAL_SEARCH_HTML_CACHE[html_cache_key] = rendered
    return rendered


SUGGESTION_TITLE_LIMIT = 50_000
_SUGGESTION_INDEX = {}


def suggestion_index():
    """Lazily build title and vocabulary phrases from the app's own catalog and synonym tables."""
    if not _SUGGESTION_INDEX:
        top = heapq.nlargest(SUGGESTION_TITLE_LIMIT, MOVIES, key=movie_rank_key)
        _SUGGESTION_INDEX["titles"] = [
            (normalize_text(movie["title"]).replace(",", " ").strip(), movie["title"]) for movie in top if movie.get("title")
        ]
        phrases = [(f"{genre} movies", "genre") for genre in GENRE_SYNONYMS]
        phrases += [(f"something {mood}", "mood") for mood in MOOD_SYNONYMS]
        phrases += [(f"movies about {theme}", "theme") for theme in THEME_SYNONYMS]
        _SUGGESTION_INDEX["phrases"] = phrases
    return _SUGGESTION_INDEX


@app.route("/suggest", methods=["GET"])
def suggest():
    typed = normalize_title(request.args.get("q", "")[:100])
    if len(typed) < 2:
        return jsonify([])
    index = suggestion_index()

    def rank(text):
        text = normalize_title(text)
        if text == typed:
            return 0
        if text.startswith(typed):
            return 2
        if typed in text:
            return 3
        if any(word.startswith(typed) for word in text.split()):
            return 2
        return None

    suggestions = []
    series_titles = set()
    for group in derive_series_groups(MOVIES):
        for title in group["titles"]:
            series_titles.add(title)
    for phrase, kind in index["phrases"]:
        level = rank(phrase)
        if level is not None:
            suggestions.append((level, 0, phrase, kind))
    seen = set()
    for position, (normalized_title, title) in enumerate(index["titles"]):
        level = rank(normalized_title)
        if level is not None and title not in seen:
            seen.add(title)
            is_series = title in series_titles
            kind = "series" if is_series else "title"
            series_level = 1 if is_series and (normalized_title.startswith(typed) or typed in normalized_title) else level
            suggestions.append((series_level, position + 1, title, kind))
    suggestions.sort(key=lambda item: (item[0], item[1], item[2].casefold(), item[2], item[3]))
    return jsonify([{"text": text, "kind": kind} for _, _, text, kind in suggestions[:8]])


@app.route("/feedback", methods=["POST"])
def submit_feedback():
    query = request.form.get("query", "").strip()
    movie_id = request.form.get("movie_id", "").strip()
    relevant_value = request.form.get("relevant", "")
    if not query or len(query) > 500 or not movie_id.isdigit() or relevant_value not in {"0", "1"}:
        return "Invalid feedback.", 400

    movie = next((item for item in MOVIES if str(item.get("id")) == movie_id), None)
    if movie is None:
        return "Unknown movie.", 404

    record_feedback(query, movie["id"], movie["title"], relevant_value == "1", FEEDBACK_DB_PATH)
    return redirect(url_for("index", q=query, feedback="saved"))


if __name__ == "__main__":
    app.run(debug=True)
