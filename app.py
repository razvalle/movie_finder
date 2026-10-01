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

import re
import math
import heapq
import logging
import json
from pathlib import Path
import pycountry

from flask import Flask, render_template, request

from data.movies import MOVIES
from nlp.extract import extract_preferences
from nlp.normalize import normalize_text
from nlp.query import build_structured_query, detect_country
from nlp.tfidf import load_or_build_tfidf_model
from nlp.scoring import is_excluded, rank_movies
from nlp.explain import explain_match, title_case
from nlp.conversation import build_assistant_message
from nlp.smart_search import MAX_CANDIDATES, allow_search, understand_query, retrieve_candidates, rerank_candidates
from nlp.cast_verification import classify_cast_gender, evaluate_cast_constraint

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
QUERY_LOGGER = logging.getLogger("movie_finder.query")
QUERY_LOGGER.setLevel(logging.INFO)

# Built once at startup -- rebuilding per-request would be wasteful
# since the movie corpus doesn't change while the server is running.
CATALOG_PATH = Path(__file__).parent / "data" / "imdb_movies.json"
TFIDF_CACHE_PATH = Path(__file__).parent / "data" / "tfidf_cache.pkl"
TFIDF_MODEL = load_or_build_tfidf_model(MOVIES, TFIDF_CACHE_PATH, CATALOG_PATH)
TITLE_INDEX = {}
TITLE_TOKEN_INDEX = {}
for movie in MOVIES:
    normalized_title = normalize_text(movie["title"]).replace(",", " ").strip()
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
DEFAULT_ALTERNATIVE_QUERIES = [
    "popular comedy movies",
    "highly rated science fiction movies",
    "mystery movies with an investigation",
]
COMMON_LANGUAGE_LABELS = {
    "ar": "Arabic", "de": "German", "en": "English", "es": "Spanish",
    "fr": "French", "hi": "Hindi", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "pt": "Portuguese", "ru": "Russian", "zh": "Chinese",
}

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
    poster_path = movie.get("poster_path")
    if isinstance(poster_path, str) and poster_path.startswith("/"):
        return f"https://image.tmdb.org/t/p/w342{poster_path}"
    return ""


def movie_origin_label(movie):
    countries = movie.get("production_countries") or []
    if isinstance(countries, str):
        countries = [countries]
    labels = [COUNTRY_LABELS.get(code, code) for code in countries if isinstance(code, str) and code]
    return ", ".join(dict.fromkeys(labels)) if labels else ""


def movie_rank_key(movie):
    return (movie.get("vote_count", 0), movie.get("average_rating") or 0, movie.get("release_year", 0))


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


def language_options(movies):
    codes = {str(movie.get("original_language") or "").strip().lower() for movie in movies}
    codes.discard("")
    if not codes:
        codes = set(COMMON_LANGUAGE_LABELS)
    options = []
    for code in sorted(codes):
        language = pycountry.languages.get(alpha_2=code) or pycountry.languages.get(alpha_3=code)
        options.append((code, COMMON_LANGUAGE_LABELS.get(code, getattr(language, "name", code.upper()))))
    return options


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
    normalized_query = normalize_text(query).replace(",", " ")
    exact_matches = TITLE_INDEX.get(normalized_query.strip(), [])
    if exact_matches:
        allowed_ids = {movie["id"] for movie in movies}
        return [movie for movie in exact_matches if movie["id"] in allowed_ids]
    if re.search(r"\b(?:no|not|without|except|excluding|avoid|avoiding|skip|never)\b", normalized_query):
        return []
    query_tokens = [
        token for token in normalized_query.split()
        if len(token) >= 4 and token not in GENERIC_TITLE_TOKENS
    ]
    candidate_lists = [TITLE_TOKEN_INDEX[token] for token in query_tokens if token in TITLE_TOKEN_INDEX]
    if not candidate_lists:
        return []
    candidates = min(candidate_lists, key=len)
    allowed_ids = {movie["id"] for movie in movies}
    matches = []
    for movie in candidates:
        if movie["id"] not in allowed_ids:
            continue
        normalized_title = normalize_text(movie["title"]).replace(",", " ")
        title_pattern = rf"(?<![a-z0-9]){re.escape(normalized_title)}(?![a-z0-9])"
        title_words = normalized_title.split()
        explicitly_named = re.search(rf"\b(?:called|named|titled)\s+{re.escape(normalized_title)}\b", normalized_query)
        if len(title_words) >= 2 and re.search(title_pattern, normalized_query):
            matches.append(movie)
        elif len(title_words) == 1 and explicitly_named:
            matches.append(movie)
    if not matches:
        return []
    longest_title_length = max(
        len(normalize_text(movie["title"]).replace(",", " ").split())
        for movie in matches
    )
    return [
        movie for movie in matches
        if len(normalize_text(movie["title"]).replace(",", " ").split()) == longest_title_length
    ]


def remove_title_text(query, title_matches):
    """Remove a detected title so its words cannot become search preferences."""
    normalized = normalize_text(query).replace(",", " ")
    for movie in title_matches:
        title = normalize_text(movie["title"]).replace(",", " ").strip()
        normalized = re.sub(rf"(?<![a-z0-9]){re.escape(title)}(?![a-z0-9])", " ", normalized)
    return " ".join(normalized.split())


def is_exact_title_query(query, title_matches):
    """True when the entire query is one movie title, ignoring case and punctuation."""
    normalized_query = normalize_text(query).replace(",", " ").strip()
    return any(normalized_query == normalize_text(movie["title"]).replace(",", " ").strip()
               for movie in title_matches)


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


def build_preference_tags(preferences, excluded_count):
    """Turns the preference dict into a list of small display tags for the template."""
    tags = []

    for g in preferences["genres"]:
        tags.append({"type": "include", "label": "Genre", "value": title_case(g)})
    for m in preferences["moods"]:
        tags.append({"type": "include", "label": "Mood", "value": title_case(m)})
    for t in preferences["themes"]:
        tags.append({"type": "include", "label": "Theme", "value": title_case(t)})
    for g in preferences["excluded_genres"]:
        tags.append({"type": "exclude", "label": "Exclude", "value": title_case(g)})
    for m in preferences["excluded_moods"]:
        tags.append({"type": "exclude", "label": "Exclude", "value": title_case(m)})
    for t in preferences["excluded_themes"]:
        tags.append({"type": "exclude", "label": "Exclude", "value": title_case(t)})

    rt = preferences["runtime"]
    if rt["max"] is not None and rt["min"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"{rt['min']}-{rt['max']} min"})
    elif rt["max"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"< {rt['max']} min"})
    elif rt["min"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"> {rt['min']} min"})
    elif rt["target"] is not None:
        tags.append({"type": "neutral", "label": "Runtime", "value": f"~ {rt['target']} min"})

    ry = preferences["release_year"]
    if ry:
        if ry["min"] and ry["max"]:
            tags.append({"type": "neutral", "label": "Year", "value": f"{ry['min']}-{ry['max']}"})
        elif ry["min"]:
            tags.append({"type": "neutral", "label": "Year", "value": f"after {ry['min']}"})
        elif ry["max"]:
            tags.append({"type": "neutral", "label": "Year", "value": f"before {ry['max']}"})

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


@app.route("/", methods=["GET"])
def index():
    query = request.args.get("q", "").strip()
    if len(query) > 500:
        query = query[:500]
    if query and not allow_search(request.remote_addr or "unknown"):
        return "Search limit reached. Please wait a minute and try again.", 429
    debug_enabled = request.args.get("debug", "").strip().lower() in {"1", "true", "yes"}
    selected_genre = request.args.get("genre", "").strip().lower()
    selected_language = request.args.get("language", "").strip().lower()
    selected_nationality = request.args.get("nationality", "").strip().upper()
    award_filter = request.args.get("awards", "").strip().lower() == "winners"
    initial_query = build_structured_query(query) if query else None
    detected_nationality = initial_query["country"] if initial_query else ""
    filter_conflicts = []
    if selected_nationality and detected_nationality and selected_nationality != detected_nationality:
        filter_conflicts.append("The country in your search overrides the country menu selection.")
    effective_nationality = detected_nationality or selected_nationality
    nationality_query = initial_query["cleaned_text"] if initial_query else query
    query_title_matches = find_title_matches(nationality_query, MOVIES) if query else []
    preference_query = remove_title_text(nationality_query, query_title_matches)
    query_analysis = build_structured_query(query, preference_query) if query else None
    query_preferences = query_analysis["preferences"] if query_analysis else None
    if query_preferences and query_preferences["genres"]:
        if selected_genre and selected_genre not in query_preferences["genres"]:
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
    ))
    if title_only_intent:
        if not selected_genre:
            effective_nationality = ""
    understanding = understand_query(query) if query else None
    try:
        per_page = int(request.args.get("per_page", DEFAULT_PER_PAGE))
    except ValueError:
        per_page = DEFAULT_PER_PAGE
    if per_page not in PER_PAGE_OPTIONS:
        per_page = DEFAULT_PER_PAGE
    try:
        page_number = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page_number = 1
    genres = sorted({genre for movie in MOVIES for genre in movie["genres"]})
    movie_languages = language_options(MOVIES)
    available_languages = {str(movie.get("original_language") or "").strip().lower() for movie in MOVIES}
    available_languages.discard("")
    nationalities = sorted({
        code for movie in MOVIES for code in movie_country_codes(movie) if code
    })
    llm_year_range = understanding["year_range"] if understanding else {}
    llm_min_rating = understanding["min_rating"] if understanding else None
    query_genres = [] if selected_genre else (list(query_preferences["genres"]) if query_preferences else [])
    if selected_genre and query_preferences:
        query_preferences["genres"] = [selected_genre]
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

    year_constraint = query_preferences["release_year"] if query_preferences else None

    def matching_base_filters(movie):
        return (
            (not selected_genre or selected_genre in movie["genres"])
            and (not selected_language or not available_languages or str(movie.get("original_language") or "").strip().lower() == selected_language)
            and (not effective_nationality or effective_nationality in movie_country_codes(movie))
            and (not award_filter or movie.get("notable_awards"))
        )

    base_movies = [movie for movie in MOVIES if matching_base_filters(movie)]

    def search_pool(include_genres=True, include_year=True, include_rating=True, include_cast=True):
        pool = []
        for movie in base_movies:
            if include_genres and query_genres and not any(genre in movie["genres"] for genre in query_genres):
                continue
            if query_preferences and is_excluded(movie, query_preferences):
                continue
            if include_cast and query_preferences and query_preferences.get("cast_gender") and not evaluate_cast_constraint(movie, query_preferences["cast_gender"]):
                continue
            if include_year and not movie_matches_release_year(movie, year_constraint):
                continue
            rating = movie.get("average_rating")
            if include_rating and llm_min_rating is not None and (rating is None or rating < llm_min_rating):
                continue
            pool.append(movie)
        return pool

    filtered_movies = search_pool()
    search_notice = ""
    if query and not filtered_movies:
        relaxed_year_rating = search_pool(include_year=False, include_rating=False)
        if relaxed_year_rating:
            filtered_movies = relaxed_year_rating
            search_notice = "Showing closest matches; year or rating limits were relaxed."
        elif query_genres and not selected_genre:
            relaxed_genre = search_pool(include_genres=False, include_year=False, include_rating=False)
            if relaxed_genre:
                filtered_movies = relaxed_genre
                search_notice = "Showing closest matches; genre, year, or rating limits were relaxed."
        if not filtered_movies:
            filtered_movies = search_pool(include_genres=False, include_year=False, include_rating=False)
            if filtered_movies:
                search_notice = "Showing closest matches; descriptive filters were relaxed."
        if not filtered_movies and query_preferences and query_preferences.get("cast_gender"):
            filtered_movies = search_pool(
                include_genres=False,
                include_year=False,
                include_rating=False,
                include_cast=False,
            )

    context = {
        "query": query,
        "selected_genre": selected_genre,
        "selected_language": selected_language,
        "language_options": movie_languages,
        "language_metadata_available": bool(available_languages),
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
        "preference_tags": [],
        "results": [],
        "movies": [],
        "searched": False,
        "result_count": len(filtered_movies),
        "result_count_label": "",
        "query_debug": query_analysis["debug"] if query_analysis else {},
        "debug_enabled": debug_enabled,
        "debug_view": None,
    }

    if not query:
        filter_metadata_missing = bool(
            selected_language and not available_languages
        )
        context["assistant_state"] = "unmapped" if filter_metadata_missing else ""
        if filter_metadata_missing:
            context["alternative_queries"] = DEFAULT_ALTERNATIVE_QUERIES
        context["assistant_message"] = build_assistant_message(
            "",
            len(filtered_movies),
            selected_language=selected_language,
            language_available=bool(available_languages),
        )

    if query:
        preferences = query_preferences
        cast_constraint = preferences.get("cast_gender")
        negative_cast_constraint = bool(
            cast_constraint
            and (cast_constraint.get("exclude") == "female" or cast_constraint.get("require") == "all_male")
        )
        QUERY_LOGGER.info("interpreted_query=%s", query_analysis["debug"])
        if has_unsupported_negated_requirement(query) and not query_preferences.get("cast_gender"):
            context.update({
                "searched": True,
                "result_count": 0,
                "result_count_label": "0 closest matches",
                "results": [],
                "search_notice": "No close matches found. This catalog cannot verify strict gender-based negatives such as 'no woman', so no movie can be confirmed as a true match.",
                "assistant_message": build_assistant_message(query, 0),
                "assistant_state": "empty",
                "alternative_queries": [
                    "movies with women",
                    "movies featuring a strong female lead",
                    "popular dramas and comedies",
                ],
            })
            return render_template("index.html", **context)
        normalized_query = normalize_text(nationality_query).replace(",", " ").strip()
        exact_title_exists = normalized_query in TITLE_INDEX
        nationality_only = detected_nationality and not title_only_intent and not exact_title_exists and not any((
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
        candidates = retrieve_candidates(query, understanding, search_movies, TFIDF_MODEL)
        has_structured_constraint = bool(query_preferences and (
            query_preferences.get("cast_gender")
            or query_preferences.get("excluded_genres")
            or query_preferences.get("excluded_content_descriptors")
            or query_preferences.get("release_year")
            or query_preferences.get("runtime", {}).get("min")
            or query_preferences.get("runtime", {}).get("max")
        )) or bool(understanding.get("hard_constraints"))
        if not candidates and has_structured_constraint and filtered_movies:
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
        candidates = rerank_candidates(query, candidates, understanding)
        candidate_movies = [item["movie"] for item in candidates]
        scoring_preferences = dict(ranking_preferences)
        scoring_preferences["free_text_keywords"] = []
        scoring_preferences["moods"] = []
        scoring_preferences["themes"] = []
        ranking = rank_movies(candidate_movies, scoring_preferences, TFIDF_MODEL)
        candidate_by_id = {str(item["movie"]["id"]): item for item in candidates}
        all_results = ranking["results"]
        max_votes = max((item["movie"].get("vote_count", 0) for item in candidates), default=0)
        for entry in all_results:
            candidate = candidate_by_id[str(entry["movie"]["id"])]
            entry["match_reason"] = candidate["match_reason"]
            entry["match_level"] = candidate.get("match_level", "partial")
            entry["satisfied"] = candidate.get("satisfied", [])
            entry["unsatisfied"] = candidate.get("unsatisfied", [])
            entry["unverifiable"] = candidate.get("unverifiable", [])
            entry["confidence"] = candidate.get("confidence", 0)
            entry["verification_fallback"] = candidate.get("verification_fallback", False)
            popularity = math.log1p(entry["movie"].get("vote_count", 0)) / math.log1p(max_votes) if max_votes else 0
            if candidate.get("final_score") is None:
                candidate["final_score"] = candidate["search_score"] * 0.78 + entry["percent"] * 0.20
            entry["final_score"] = candidate["final_score"] + popularity * 2
        exact_results = [entry for entry in all_results if entry["match_level"] == "full"]
        if exact_results:
            all_results = exact_results
            context["result_count_label"] = f"{len(all_results)} exact matches"
        else:
            all_results.sort(key=lambda entry: (
                len(entry["satisfied"]), entry["confidence"], entry["movie"].get("average_rating") or 0,
                entry["movie"].get("vote_count", 0),
            ), reverse=True)
            context["result_count_label"] = f"{len(all_results)} closest matches"
        if exact_results and query_analysis["ranking_intent"]:
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
            cast_status = classify_cast_gender(movie)
            if cast_constraint and not negative_cast_constraint:
                constraint_match = evaluate_cast_constraint(movie, cast_constraint)
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
            display_results.append({
                "rank": rank,
                "movie": movie,
                "percent": entry["percent"],
                "poster_exists": poster_exists(movie),
                "genres_display": ", ".join(title_case(g) for g in movie["genres"]),
                "reasons": explain_match(movie, ranking_preferences, entry["breakdown"]),
                "match_reason": entry["match_reason"],
                "match_level": entry["match_level"],
                "match_badge": "Exact match" if entry["match_level"] == "full" else "Closest match",
                "show_unverified_badge": bool(entry["unverifiable"] or entry["verification_fallback"]),
                "cast_status": cast_status,
                "cast_badge": cast_status["badge"],
                "cast_evidence": cast_status["evidence"],
                "show_cast_evidence": bool(cast_constraint),
            })

        if all_results and not exact_results:
            relaxation_note = search_notice if "relaxed" in search_notice.lower() else ""
            search_notice = "We couldn't find a movie that exactly matches your request. Here are the closest matches."
            if negative_cast_constraint:
                search_notice += " None of these are confirmed to have no female characters."
            missing = list(dict.fromkeys(
                item for entry in all_results
                for item in entry["unsatisfied"] + entry["unverifiable"]
            ))
            if missing:
                search_notice += " Missing or unverified: " + "; ".join(missing[:3]) + "."
            if relaxation_note:
                search_notice += " " + relaxation_note
        if any(entry["verification_fallback"] for entry in all_results) or not understanding.get("llm_used"):
            fallback_notice = "Smart matching is unavailable right now, showing basic results."
            search_notice = f"{fallback_notice} {search_notice}" if search_notice else fallback_notice
        missing_requirements = list(dict.fromkeys(
            item for entry in all_results
            for item in entry["unsatisfied"] + entry["unverifiable"]
        ))
        context["assistant_state"] = "exact" if exact_results else "partial" if all_results else "empty"
        context["assistant_message"] = build_assistant_message(
            query,
            len(all_results),
            exact_count=len(exact_results),
            selected_language=selected_language,
            language_available=bool(available_languages),
            genres=(understanding.get("genres") or query_preferences.get("genres", [])),
            understood_terms=understanding.get("keywords", []) or understanding.get("expanded_concepts", []),
            missing_requirements=missing_requirements,
        )
        context.update({
            "preference_tags": build_preference_tags(ranking_preferences, ranking["excluded_count"]),
            "results": display_results,
            "searched": True,
            "result_count": len(all_results),
            "page_number": page_number,
            "total_pages": total_pages,
            "debug_view": build_debug_view(query_analysis, ranking_preferences, len(filtered_movies), len(all_results)),
            "search_notice": search_notice,
            "assistant_message": context["assistant_message"],
            "assistant_state": context["assistant_state"],
        })
        if not all_results:
            context["result_count_label"] = "0 closest matches"
            context["alternative_queries"] = understanding["alternative_queries"] or DEFAULT_ALTERNATIVE_QUERIES
            if "No close matches found" not in search_notice:
                search_notice = (search_notice + " " if search_notice else "") + "No close matches found. Try one of these searches:"
            context["search_notice"] = search_notice
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

    return render_template("index.html", **context)


if __name__ == "__main__":
    app.run(debug=True)
