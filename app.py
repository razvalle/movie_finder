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
import unicodedata
from collections import Counter
from pathlib import Path
import pycountry

from flask import (
    Flask, g, has_request_context, jsonify, make_response, redirect,
    render_template, request, url_for,
)

from data.movies import MOVIES
from data.synonyms import GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS
from feedback_store import FEEDBACK_DB_PATH, record_feedback
from poster_storage import poster_public_base_url, poster_public_url
from nlp.extract import extract_preferences
from nlp.normalize import normalize_text
from nlp.query import build_structured_query, detect_country
from nlp.tfidf import load_or_build_tfidf_model
from nlp.scoring import is_excluded, rank_movies
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
from nlp.concepts import normalize_phrase
from nlp.cast_verification import classify_cast_gender, evaluate_cast_constraint
from nlp.search_diagnostics import SearchTrace, write_search_record

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
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
TFIDF_MODEL = load_or_build_tfidf_model(MOVIES, TFIDF_CACHE_PATH, CATALOG_PATH)
TITLE_INDEX = {}
TITLE_EXACT_INDEX = {}
TITLE_TOKEN_INDEX = {}


def _title_key(value, fold_accents=True):
    normalized = normalize_text(value)
    if fold_accents:
        normalized = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()


for movie in MOVIES:
    normalized_title = _title_key(movie["title"])
    TITLE_EXACT_INDEX.setdefault(_title_key(movie["title"], fold_accents=False), []).append(movie)
    TITLE_INDEX.setdefault(normalized_title, []).append(movie)
    for token in set(normalized_title.split()):
        if len(token) >= 4:
            TITLE_TOKEN_INDEX.setdefault(token, []).append(movie)

EXAMPLE_QUERIES = [
    "I want something suspenseful but not horror, preferably a mystery under two hours.",
    "I want something funny but not romance, around 100 minutes.",
    "Give me a scary movie but no gore, at least 100 minutes.",
    "Gusto ko ng suspenseful na mystery movie, less than 130 minutes, walang horror.",
]
PER_PAGE_OPTIONS = (12, 24, 48)
DEFAULT_PER_PAGE = 12


def _run_search_stage(name, operation, fallback, trace=None):
    trace = trace or getattr(g, "search_trace", None)
    if trace is not None:
        trace.stage = name
        if trace.budget_exceeded:
            trace.report_budget_exceeded(name)
            trace.fallback(name)
            return fallback
    try:
        value = operation()
    except Exception as error:
        if trace is not None:
            trace.error(name, error)
            trace.fallback(name)
        return fallback
    if trace is not None and trace.budget_exceeded:
        trace.report_budget_exceeded(name)
        trace.fallback(name)
        return fallback
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


def _emergency_search_response(query):
    notice = (
        "Search could not safely prepare results. No filters were relaxed. "
        "Remove a filter or try a shorter query."
    )
    context = dict(getattr(g, "search_context", None) or {})
    context.update({
        "query": str(query or "")[:MAX_QUERY_LENGTH],
        "search_notice": notice,
        "assistant_message": notice,
        "assistant_state": "empty",
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
        "alternative_queries": context.get("alternative_queries", []),
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
    for phrase in sorted(phrases, key=len, reverse=True):
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
    return (movie.get("vote_count", 0), movie.get("average_rating") or 0, movie.get("release_year", 0))


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


def ranking_key(entry, ranking_intent):
    movie = entry["movie"]
    rating = movie.get("average_rating") or 0
    votes = movie.get("vote_count") or 0
    year = movie.get("release_year") or 0
    if ranking_intent == "popular":
        return (votes, rating, entry["percent"], year)
    if ranking_intent == "best":
        return (rating, votes, entry["percent"], year)
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


def movie_matches_release_year(movie, constraint):
    """Apply an explicit release-year request as a hard catalog filter."""
    if not constraint:
        return True
    year = movie.get("release_year")
    if year is None:
        return False
    return (
        (constraint["min"] is None or year >= constraint["min"])
        and (constraint["max"] is None or year <= constraint["max"])
    )


GENERIC_TITLE_TOKENS = {
    "where", "there", "here", "when", "what", "which", "who", "why", "how",
    "movie", "movies", "film", "films", "is", "are", "was", "were", "am",
    "be", "been", "being", "a", "an", "the", "do", "does", "did",
    "have", "has", "had", "would", "could", "should",
}


def find_title_matches(query, movies):
    """Find titles written exactly or as a phrase inside a natural-language query."""
    normalized_query = _title_key(query)
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


def remove_title_text(query, title_matches):
    """Remove a detected title so its words cannot become search preferences."""
    normalized = _title_key(query)
    for movie in title_matches:
        title = _title_key(movie["title"])
        normalized = re.sub(rf"(?<![a-z0-9]){re.escape(title)}(?![a-z0-9])", " ", normalized)
    return " ".join(normalized.split())


def is_exact_title_query(query, title_matches):
    """True when the entire query is one movie title, ignoring case and punctuation."""
    normalized_query = _title_key(query)


def _contains_complete_title(query, title):
    normalized_query = _title_key(query)
    normalized_title = _title_key(title)
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_title)}(?![a-z0-9])",
        normalized_query,
    ))
    normalized_without_article = re.sub(r"^(?:the|a|an)\s+", "", normalized_query)
    return any(
        normalized_query == _title_key(movie["title"])
        or normalized_without_article == _title_key(movie["title"])
        for movie in title_matches
    )


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
        elif kind == "year":
            preferences["release_year"] = None
            if understanding:
                understanding["year_range"] = {"from": None, "to": None}
                understanding["filters"]["year_range"] = {"from": None, "to": None}
                understanding["hard_constraints"] = [c for c in understanding["hard_constraints"] if c != "release year"]


def movie_matches_runtime_limits(movie, preferences):
    """Hard check for explicit min/max runtime; unknown runtime (stored as 0) cannot satisfy a limit."""
    runtime = (preferences or {}).get("runtime") or {}
    low, high = runtime.get("min"), runtime.get("max")
    if low is None and high is None:
        return True
    length = movie.get("runtime") or 0
    return length > 0 and (low is None or length >= low) and (high is None or length <= high)


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

    rt = preferences["runtime"]
    if rt["max"] is not None and rt["min"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"{rt['min']}-{rt['max']} min", "key": "runtime"})
    elif rt["max"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"< {rt['max']} min", "key": "runtime"})
    elif rt["min"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"> {rt['min']} min", "key": "runtime"})
    elif rt["target"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"~ {rt['target']} min", "key": "runtime"})

    ry = preferences["release_year"]
    if ry:
        if ry["min"] and ry["max"]:
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


def _result_tier_counts(results):
    counts = {"exact": 0, "close": 0, "related": 0}
    for result in results:
        tier = result.get("match_tier", "close")
        counts[tier if tier in counts else "close"] += 1
    return counts


def _unverified_candidates(candidates):
    results = []
    for candidate in candidates[:MAX_CANDIDATES]:
        item = copy.deepcopy(candidate)
        item.update({
            "match_level": "partial",
            "match_tier": "close",
            "match_eligible": False,
            "estimated_percent": 0,
            "evidence_strength": 0.0,
            "satisfied": [],
            "unsatisfied": [],
            "unverifiable": ["catalog evidence could not be verified"],
            "confidence": 0,
            "verification_fallback": True,
            "fallback_only": True,
        })
        item.setdefault(
            "match_reason",
            "Shown as a closest alternative because evidence could not be verified.",
        )
        results.append(item)
    return results


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
    query_notice = (
        f"Search was limited to the first {MAX_QUERY_LENGTH} characters."
        if query_was_capped else ""
    )
    trace = getattr(g, "search_trace", None)
    if trace is not None:
        trace.count("input", 1 if query else 0)
    _run_search_stage(
        "normalize",
        lambda: normalize_text(query),
        query.casefold(),
        trace,
    )
    if query and not allow_search(request.remote_addr or "unknown"):
        return "Search limit reached. Please wait a minute and try again.", 429
    debug_enabled = request.args.get("debug", "")[:16].strip().lower() in {"1", "true", "yes"}
    selected_genres = list(dict.fromkeys(
        value[:64].strip().lower()
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
    initial_query = _run_search_stage(
        "parse",
        lambda: build_structured_query(query),
        None,
        trace,
    ) if query else None
    if query and initial_query is None:
        initial_query = _minimal_query_analysis(query, trace)
    filter_conflicts = []
    query_title_matches = _run_search_stage(
        "title-match",
        lambda: find_title_matches(query, MOVIES),
        [],
        trace,
    ) if query else []
    if trace is not None:
        trace.count("title-matches", len(query_title_matches))
    if query_title_matches:
        normalized_title_query = _title_key(query)
        title_query_without_article = re.sub(
            r"^(?:the|a|an)\s+", "", normalized_title_query,
        )
        title_prefix_query = not is_exact_title_query(query, query_title_matches) and any(
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
            lambda: build_structured_query(preference_query),
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
            lambda: find_title_matches(nationality_query, MOVIES),
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
            lambda: build_structured_query(query, preference_query),
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
        lambda: copy.deepcopy(interpret_query(query)),
        None,
        trace,
    ) if query else None
    if query and understanding is None:
        understanding = _minimal_understanding(query, query_analysis)
    if query_title_matches:
        raw_understanding = understanding
        understanding = _run_search_stage(
            "parse",
            lambda: copy.deepcopy(interpret_query(preference_query)),
            None,
            trace,
        )
        if understanding is None:
            understanding = _minimal_understanding(preference_query, query_analysis)
        if raw_understanding and raw_understanding.get("similar_to"):
            understanding["similar_to"] = raw_understanding["similar_to"]
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
    genres = sorted({genre for movie in MOVIES for genre in movie.get("genres", [])})
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
        llm_year_range = understanding["year_range"]

    year_constraint = query_preferences["release_year"] if query_preferences else None

    def matching_base_filters(movie):
        return (
            (not selected_genres or any(genre in movie["genres"] for genre in selected_genres))
            and (not effective_nationality or effective_nationality in movie_country_codes(movie))
            and (not selected_awards or movie_matches_recognition(movie, selected_awards))
        )

    base_movies = [movie for movie in MOVIES if matching_base_filters(movie)]
    if trace is not None:
        trace.count("base-filtered", len(base_movies))

    def search_pool():
        pool = []
        for movie in base_movies:
            if query_genres:
                genre_match = (
                    any(genre in movie["genres"] for genre in query_genres)
                    if re.search(r"\bor\b", preference_query, re.IGNORECASE)
                    else all(genre in movie["genres"] for genre in query_genres)
                )
                if not genre_match:
                    continue
            if not movie_matches_runtime_limits(movie, query_preferences):
                continue
            if query_preferences and is_excluded(movie, query_preferences):
                continue
            if query_preferences and query_preferences.get("cast_gender") and not evaluate_cast_constraint(movie, query_preferences["cast_gender"]):
                continue
            if not movie_matches_release_year(movie, year_constraint):
                continue
            rating = movie.get("average_rating")
            if llm_min_rating is not None and (rating is None or rating < llm_min_rating):
                continue
            pool.append(movie)
        return pool

    filtered_movies = _run_search_stage("filter", search_pool, [], trace)
    if trace is not None:
        trace.count("filtered", len(filtered_movies))
    search_notice = query_notice
    normalization_notice = ""

    context = {
        "query": query,
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
    g.search_context = context

    if not query:
        context["assistant_message"] = build_assistant_message("", len(filtered_movies))

    if query:
        unsupported = query_analysis["unsupported_filters"]
        unsupported_requests = []
        if unsupported["people"] and not any(movie.get("cast") or movie.get("actors") for movie in MOVIES):
            unsupported_requests.append("cast and named-person credits")
        if unsupported["languages"] and not any(
            movie.get("original_language") or movie.get("language") for movie in MOVIES
        ):
            unsupported_requests.append("original-language metadata")
        if unsupported.get("certifications") and not any(
            movie.get("certification") or movie.get("content_rating") or movie.get("rating_certificate")
            for movie in MOVIES
        ):
            unsupported_requests.append("certification ratings")
        if unsupported_requests:
            context.update({
                "searched": True,
                "result_count": 0,
                "result_count_label": "0 exact, 0 close, 0 related",
                "results": [],
                "search_notice": (
                    "This catalog does not include " + ", ".join(unsupported_requests)
                    + ". Try a search using supported genre, year, runtime, or country information."
                ),
                "assistant_message": (
                    "This catalog does not include " + ", ".join(unsupported_requests)
                    + ". Try a search using supported genre, year, runtime, or country information."
                ),
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
            return render_template("index.html", **context)
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
        normalized_query = _title_key(nationality_query)
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
        ranking_preferences = preferences
        if title_matches and is_exact_title_query(query, title_matches):
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
                _contains_complete_title(query, movie["title"])
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
                lambda: retrieve_candidates(query, retrieval_understanding, search_movies, TFIDF_MODEL),
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
        candidates = _run_search_stage(
            "verify-rerank",
            lambda: rerank_candidates(
                query,
                candidates,
                verification_understanding,
                on_error=trace.error if trace is not None else None,
            ),
            _unverified_candidates(candidates),
            trace,
        )
        if trace is not None:
            trace.count("verified", len(candidates))
        candidate_movies = [item["movie"] for item in candidates]
        scoring_preferences = dict(ranking_preferences)
        normalized_terms = next((
            item.get("normalized_terms") for item in candidates
            if item.get("normalized_terms")
        ), None)
        if normalized_terms:
            scoring_preferences["free_text_keywords"] = normalized_terms
        evidence_by_id = {
            str(item["movie"]["id"]): item.get("query_evidence")
            for item in candidates if item.get("query_evidence")
        }
        relevance_by_id = {
            str(item["movie"]["id"]): item.get("relevance_score")
            for item in candidates if item.get("relevance_score") is not None
        }
        ranking = _run_search_stage(
            "score",
            lambda: rank_movies(
                candidate_movies,
                scoring_preferences,
                TFIDF_MODEL,
                evidence_by_id=evidence_by_id,
                relevance_by_id=relevance_by_id,
                apply_filters=False,
            ),
            {
                "results": [{
                    "movie": item["movie"],
                    "percent": 0,
                    "breakdown": {},
                } for item in candidates],
                "excluded_count": 0,
            },
            trace,
        )
        candidate_by_id = {str(item["movie"]["id"]): item for item in candidates}
        all_results = ranking["results"]
        if trace is not None:
            trace.count("scored", len(all_results))
        for entry in all_results:
            candidate = candidate_by_id.get(str(entry["movie"]["id"]))
            if candidate is None:
                continue
            entry["match_reason"] = candidate["match_reason"]
            entry["satisfied"] = candidate.get("satisfied", [])
            entry["unsatisfied"] = candidate.get("unsatisfied", [])
            entry["unverifiable"] = candidate.get("unverifiable", [])
            entry["confidence"] = candidate.get("confidence", 0)
            entry["verification_fallback"] = candidate.get("verification_fallback", False)
            candidate["percent"] = entry["percent"]
            candidate["final_score"] = entry["percent"]
            candidate["match_level"] = (
                "full"
                if candidate.get("match_eligible")
                and not candidate.get("fallback_only")
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
                or is_excluded(movie, query_preferences)
            ):
                if trace is not None:
                    trace.error(
                        "self-check",
                        RuntimeError("Dropped result with invalid score or a hard-filter violation"),
                    )
                continue
            if entry.get("match_level") == "full" and (
                score < EXACT_MATCH_THRESHOLD
                or not candidate.get("match_eligible")
                or candidate.get("unsatisfied")
                or candidate.get("unverifiable")
                or candidate.get("fallback_only")
            ):
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
        if understanding.get("similar_to"):
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
                -entry["percent"],
                -entry.get("evidence_strength", 0.0),
                -(entry["movie"].get("average_rating") or 0),
                -(entry["movie"].get("vote_count") or 0),
                str(entry["movie"].get("title", "")).casefold(),
                str(entry["movie"].get("id", "")),
            ))
        else:
            all_results.sort(key=lambda entry: (
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
        if exact_results and query_analysis["ranking_intent"] and not understanding.get("similar_to"):
            all_results.sort(key=lambda entry: (
                ranking_key(entry, query_analysis["ranking_intent"]), entry["final_score"]
            ), reverse=True)
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
            if entry.get("verification_fallback") and not reasons:
                reasons = [{"ok": False, "text": entry["match_reason"]}]
            display_results.append({
                "rank": rank,
                "movie": movie,
                "percent": entry["percent"],
                "breakdown": entry["breakdown"],
                "poster_exists": poster_exists(movie),
                "genres_display": ", ".join(title_case(g) for g in movie["genres"]),
                "reasons": reasons,
                "match_reason": entry["match_reason"],
                "match_level": entry["match_level"],
                "match_tier": entry["match_tier"],
                "match_badge": {
                    "exact": "Exact match",
                    "close": "Closest match",
                    "related": "Related match",
                }[entry["match_tier"]],
                "show_unverified_badge": bool(entry["unverifiable"] or entry["verification_fallback"]),
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
            "assistant_message": context["assistant_message"],
            "assistant_state": context["assistant_state"],
        })
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
        browse_source = heapq.nlargest(start + per_page, filtered_movies, key=movie_rank_key)
        context.update({
            "movies": [
                {"movie": movie, "poster_exists": poster_exists(movie)}
                for movie in browse_source[start:start + per_page]
            ],
            "page_number": page_number,
            "total_pages": total_pages,
        })

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
    return render_template("index.html", **context)


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
    typed = normalize_text(request.args.get("q", "")[:100]).replace(",", " ").strip()
    if len(typed) < 2:
        return jsonify([])
    index = suggestion_index()

    def rank(text):
        if text.startswith(typed):
            return 0
        if any(word.startswith(typed) for word in text.split()) or f" {typed}" in text:
            return 1
        return None

    suggestions = []
    for phrase, kind in index["phrases"]:
        if rank(phrase) is not None:
            suggestions.append((rank(phrase), 0, phrase, kind))
    seen = set()
    for position, (normalized_title, title) in enumerate(index["titles"]):
        level = rank(normalized_title)
        if level is not None and title not in seen:
            seen.add(title)
            suggestions.append((level, position + 1, title, "title"))
    suggestions.sort(key=lambda item: (item[0], item[1]))
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
