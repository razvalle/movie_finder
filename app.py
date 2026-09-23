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
from pathlib import Path
import pycountry

from flask import Flask, render_template, request

from data.movies import MOVIES
from nlp.extract import extract_preferences
from nlp.normalize import normalize_text
from nlp.tfidf import load_or_build_tfidf_model
from nlp.scoring import rank_movies
from nlp.explain import explain_match, title_case

app = Flask(__name__)

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
    normalized = normalize_text(query).replace(",", " ").strip()
    for phrase, code in sorted(COUNTRY_PHRASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized):
            return code
    for phrase, code in sorted(NATIONALITY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized):
            return code
    return ""


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
        if re.search(title_pattern, normalized_query):
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
    selected_genre = request.args.get("genre", "").strip().lower()
    selected_nationality = request.args.get("nationality", "").strip().upper()
    detected_nationality = detect_nationality(query) if query else ""
    effective_nationality = detected_nationality or selected_nationality
    nationality_query = remove_nationality_text(query, detected_nationality) if detected_nationality else query
    query_title_matches = find_title_matches(nationality_query, MOVIES) if query else []
    preference_query = remove_title_text(nationality_query, query_title_matches)
    query_preferences = extract_preferences(preference_query) if query else None
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
    }

    if query:
        preferences = query_preferences
        if detected_nationality:
            preferences = dict(preferences)
            preferences["free_text_keywords"] = remove_nationality_words(
                preferences["free_text_keywords"], query, detected_nationality
            )
        normalized_query = normalize_text(query).replace(",", " ").strip()
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
        if detected_nationality and not any((
            ranking_preferences["genres"], ranking_preferences["moods"],
            ranking_preferences["themes"], ranking_preferences["free_text_keywords"],
            ranking_preferences["runtime"]["min"], ranking_preferences["runtime"]["max"],
            ranking_preferences["runtime"]["target"], ranking_preferences["release_year"],
        )):
            all_results.sort(key=lambda entry: movie_rank_key(entry["movie"]), reverse=True)
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
