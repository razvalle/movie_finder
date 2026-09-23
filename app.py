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
from nlp.scoring import rank_movies
from nlp.explain import explain_match, title_case

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


def movie_rank_key(movie):
    return (movie.get("average_rating") or 0, movie.get("vote_count", 0), movie.get("release_year", 0))


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
    structured = {
        "country": COUNTRY_LABELS.get(country, None) if country else None,
        "genres": [title_case(genre) for genre in preferences["genres"]],
        "excluded_genres": [title_case(genre) for genre in preferences["excluded_genres"]],
        "moods": [title_case(mood) for mood in preferences["moods"]],
        "themes": [title_case(theme) for theme in preferences["themes"]],
        "year": release_year,
        "ranking": query_analysis["ranking_intent"] or None,
        "semantic_description": query_analysis["semantic_description"] or None,
        "people": query_analysis["people"] or None,
        "languages": query_analysis["languages"] or None,
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


def find_title_matches(query, movies):
    """Find titles written exactly or as a phrase inside a natural-language query."""
    normalized_query = normalize_text(query).replace(",", " ")
    exact_matches = TITLE_INDEX.get(normalized_query.strip(), [])
    if exact_matches:
        allowed_ids = {movie["id"] for movie in movies}
        return [movie for movie in exact_matches if movie["id"] in allowed_ids]
    query_tokens = [token for token in normalized_query.split() if len(token) >= 4]
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
    debug_enabled = request.args.get("debug", "").strip().lower() in {"1", "true", "yes"}
    selected_genre = request.args.get("genre", "").strip().lower()
    selected_nationality = request.args.get("nationality", "").strip().upper()
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
            filter_conflicts.append("The genre in your search overrides the genre menu selection.")
        selected_genre = ""
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
        selected_genre = ""
        effective_nationality = ""
    if detected_nationality and query_preferences and not query_preferences["genres"]:
        selected_genre = ""
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
    nationalities = sorted({
        code for movie in MOVIES for code in movie_country_codes(movie) if code
    })
    filtered_movies = [
        movie for movie in MOVIES
        if (not selected_genre or selected_genre in movie["genres"])
        and (not effective_nationality or effective_nationality in movie_country_codes(movie))
    ]

    context = {
        "query": query,
        "selected_genre": selected_genre,
        "selected_nationality": effective_nationality,
        "detected_nationality": detected_nationality,
        "filter_conflicts": filter_conflicts,
        "nationality_label": COUNTRY_LABELS.get(effective_nationality, effective_nationality),
        "genres": genres,
        "nationalities": nationalities,
        "nationality_labels": COUNTRY_LABELS,
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
        "query_debug": query_analysis["debug"] if query_analysis else {},
        "debug_enabled": debug_enabled,
        "debug_view": None,
    }

    if query:
        preferences = query_preferences
        QUERY_LOGGER.info("interpreted_query=%s", query_analysis["debug"])
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
        ranking = rank_movies(search_movies, ranking_preferences, TFIDF_MODEL)
        all_results = ranking["results"]
        if query_analysis["ranking_intent"]:
            all_results.sort(key=lambda entry: ranking_key(entry, query_analysis["ranking_intent"]), reverse=True)
        total_pages = max(1, math.ceil(len(all_results) / per_page))
        page_number = min(page_number, total_pages)
        start = (page_number - 1) * per_page
        results = all_results[start:start + per_page]

        display_results = []
        for rank, entry in enumerate(results, start=1):
            movie = entry["movie"]
            display_results.append({
                "rank": rank,
                "movie": movie,
                "percent": entry["percent"],
                "poster_exists": poster_exists(movie),
                "genres_display": ", ".join(title_case(g) for g in movie["genres"]),
                "reasons": explain_match(movie, ranking_preferences, entry["breakdown"]),
            })

        context.update({
            "preference_tags": build_preference_tags(ranking_preferences, ranking["excluded_count"]),
            "results": display_results,
            "searched": True,
            "result_count": len(all_results),
            "page_number": page_number,
            "total_pages": total_pages,
            "debug_view": build_debug_view(query_analysis, ranking_preferences, len(filtered_movies), len(all_results)),
        })
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
