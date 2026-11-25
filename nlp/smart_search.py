"""Rule-based query understanding and lightweight hybrid retrieval."""

import copy
import hashlib
import gzip
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from collections import Counter, OrderedDict, defaultdict, deque
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)
from data.movies import MOVIES as _STARTUP_MOVIES
from .concepts import (
    MAX_EXPANSION_WEIGHT,
    expand_query,
    load_concept_table,
    normalize_phrase,
)
from .normalize import normalize_text
from .query import EXPLICIT_SYNTAX_TOKENS, build_structured_query
from .runtime import WORD_NUMBERS
from .scoring import is_excluded
from .similarity import cosine_similarity, vector_norm
from .tfidf import build_tfidf_model, simple_word_tokens, vectorize_query
from .catalog_matching import normalize_title


ROOT_DIR = Path(__file__).resolve().parents[1]
EMBEDDINGS_PATH = ROOT_DIR / "data" / "movie_embeddings.json.gz"
MAX_QUERY_LENGTH = 500
MAX_CANDIDATES = 30
# A full label requires verified constraints, strong term evidence, and >=80%.
EXACT_MATCH_THRESHOLD = 80
CACHE_SECONDS = 600
CACHE_SIZE = 512
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_REDIS_CLIENT = None
_REDIS_URL = None
_REDIS_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)
CONCEPT_TABLE = load_concept_table(_STARTUP_MOVIES)

_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_RATE_LIMITS = defaultdict(deque)
_RATE_LIMIT_LOCK = threading.Lock()
_BM25_CACHE = {}
_BM25_LOCK = threading.Lock()

_RATE_LIMIT_SCRIPT = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local maximum = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= maximum then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return 1
"""


def _redis_client():
    global _REDIS_CLIENT, _REDIS_URL
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        return None
    with _REDIS_LOCK:
        if _REDIS_URL != redis_url:
            try:
                import redis
                _REDIS_CLIENT = redis.Redis.from_url(redis_url, decode_responses=True)
                _REDIS_URL = redis_url
            except ImportError:
                _LOGGER.warning("REDIS_URL is set, but the redis package is unavailable; using process-local state.")
                return None
        return _REDIS_CLIENT


def _shared_key(namespace, value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"movie-finder:{namespace}:{hashlib.sha256(encoded).hexdigest()}"

SIMILAR_TO_PATTERN = re.compile(
    r"\b(?:similar to|in the style of|such as|something like|more like|(?:movies?|films?) like|like)\s+([a-z0-9][a-z0-9 :'&-]{1,100}?)"
    r"(?=\s+(?:but|with|without|that|from|in|and|except)\b|$)"
)

GENRE_ALIASES = {
    "science fiction": "sci-fi", "science-fiction": "sci-fi", "sci fi": "sci-fi",
    "scifi": "sci-fi", "rom com": "comedy", "romcom": "comedy",
    "animated": "animation", "cartoon": "animation", "cartoons": "animation",
    "biopic": "biography", "biopics": "biography",
}

LOCAL_CONCEPTS = [
    {
        "pattern": r"\b(?:dwarfs?|dwarves|dwarf|little people)\b",
        "keywords": ["dwarf", "dwarves", "little people"],
        "expanded_concepts": ["hobbit", "Middle-earth", "fantasy", "mines", "fairy tale"],
    },
    {
        "pattern": r"\brobot\b.*\b(?:love|romance|falls for|falls in love)\b|\b(?:love|romance)\b.*\brobot\b",
        "keywords": ["robot", "artificial intelligence", "romance"],
        "expanded_concepts": ["android", "human-machine relationship", "science fiction"],
    },
    {
        "pattern": r"\b(?:road trip|roadtrip)\b",
        "keywords": ["road trip", "journey", "travel"],
        "expanded_concepts": ["comedy", "friends", "cross-country trip"],
    },
    {
        "pattern": r"\b(?:same day|over and over|repeats? the same day|time loop|relives?)\b",
        "keywords": ["time loop", "repeating day", "reliving the same day"],
        "expanded_concepts": ["time loop", "temporal repetition", "resetting timeline"],
    },
    {
        "pattern": r"\b(?:space|spaceship|outer space)\b.*\b(?:scary|horror|terrifying|alien)\b|\b(?:scary|horror)\b.*\b(?:space|spaceship|outer space)\b",
        "keywords": ["space horror", "alien", "spaceship"],
        "expanded_concepts": ["deep space", "isolated crew", "extraterrestrial threat"],
    },
    {
        "pattern": r"\b(?:rat|mouse)\b.*\b(?:cook(?:s|ing)?|chef)\b|\b(?:cook(?:s|ing)?|chef)\b.*\b(?:rat|mouse)\b",
        "keywords": ["rat chef", "cooking", "restaurant"],
        "expanded_concepts": ["animated culinary story", "Paris kitchen", "ambitious chef"],
    },
    {
        "pattern": r"\bchess\b.*\b(?:prodigy|genius|young|child|player)\b|\b(?:prodigy|genius)\b.*\bchess\b",
        "keywords": ["chess", "prodigy", "young chess player"],
        "expanded_concepts": ["tournament", "chess master", "coming of age"],
    },
    {
        "pattern": r"\b(?:dragon|dragons|drgons)\b",
        "keywords": ["dragon", "dragons"],
        "expanded_concepts": ["fantasy", "mythical creature", "fire-breathing"],
    },
]

STOP_WORDS = {
    "a", "an", "and", "about", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "into", "is", "it", "me", "movie", "movies", "of", "on", "or",
    "please", "recommend", "show", "something", "that", "the", "this", "to", "want",
    "watch", "with", "film", "films", "who", "which", "would", "you", "less", "more",
    "where", "there", "here", "when", "what", "why", "how", "was", "were", "am",
    "are", "been", "being", "do", "does", "did", "have", "has", "had", "could",
    "should", "would", "any", "some", "all", "none", "another", "no", "not",
    "without", "except", "excluding", "avoid", "avoiding", "never", "must",
    "only", "there", "thing", "things",
    "ending", "end", "story",
    "gusto", "ko", "ng", "na",
}


def _cache_get(key):
    client = _redis_client()
    if client:
        try:
            encoded = client.get(_shared_key("cache", key))
            return json.loads(encoded) if encoded is not None else None
        except Exception as error:
            _LOGGER.warning("Shared search cache unavailable; using process-local cache: %s", error)
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        expires, value = entry
        if expires < now:
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(value)


def _cache_set(key, value):
    client = _redis_client()
    if client:
        try:
            client.setex(_shared_key("cache", key), CACHE_SECONDS, json.dumps(value, separators=(",", ":")))
        except Exception as error:
            _LOGGER.warning("Shared search cache unavailable; using process-local cache: %s", error)
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + CACHE_SECONDS, copy.deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


def allow_search(client_id):
    """Allow a bounded number of search requests per client per minute."""
    client = _redis_client()
    if client:
        now_ms = int(time.time() * 1000)
        window_ms = RATE_LIMIT_WINDOW_SECONDS * 1000
        key = _shared_key("rate", client_id)
        try:
            return bool(client.eval(
                _RATE_LIMIT_SCRIPT, 1, key, now_ms, window_ms,
                RATE_LIMIT_REQUESTS, f"{now_ms}:{secrets.token_hex(8)}",
            ))
        except Exception as error:
            _LOGGER.warning("Shared search rate limit unavailable; using process-local limit: %s", error)
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        events = _RATE_LIMITS[client_id]
        while events and now - events[0] > RATE_LIMIT_WINDOW_SECONDS:
            events.popleft()
        if len(events) >= RATE_LIMIT_REQUESTS:
            return False
        events.append(now)
        return True


def _string_list(value, limit=12, length=100):
    if not isinstance(value, list):
        return []
    return [item.strip()[:length] for item in value if isinstance(item, str) and item.strip()][:limit]


def _year_value(value):
    if isinstance(value, int) and not isinstance(value, bool) and 1880 <= value <= 2100:
        return value
    return None


def sanitize_understanding(value, raw_query):
    """Normalize structured query data and fill optional fields with defaults."""
    if not isinstance(value, dict):
        return local_understanding(raw_query)
    year_range = value.get("year_range")
    if not isinstance(year_range, dict):
        year_range = {}
    min_rating = value.get("min_rating")
    if isinstance(min_rating, bool) or not isinstance(min_rating, (int, float)) or not 0 <= min_rating <= 10:
        min_rating = None
    similar_to = value.get("similar_to")
    if not isinstance(similar_to, str):
        similar_to = None
    language = value.get("language_detected")
    return {
        "core_intent": value.get("core_intent", "")[:300] if isinstance(value.get("core_intent"), str) else "",
        "keywords": _string_list(value.get("keywords")),
        "expanded_concepts": _string_list(value.get("expanded_concepts")),
        "example_titles": _string_list(value.get("example_titles"), length=160),
        "genres": _sanitize_genres(value.get("genres")),
        "mood_tone": _string_list(value.get("mood_tone")),
        "year_range": {"from": _year_value(year_range.get("from")), "to": _year_value(year_range.get("to"))},
        "min_rating": float(min_rating) if min_rating is not None else None,
        "similar_to": similar_to[:160] if similar_to else None,
        "exclusions": _string_list(value.get("exclusions")),
        "language_detected": language[:12] if isinstance(language, str) else "en",
        "alternative_queries": _string_list(value.get("alternative_queries"), limit=3, length=160),
        "positive_requirements": _string_list(value.get("positive_requirements")),
        "negative_requirements": _string_list(value.get("negative_requirements")),
        "hard_constraints": _string_list(value.get("hard_constraints")),
        "soft_preferences": _string_list(value.get("soft_preferences")),
        "verifiability": value.get("verifiability") if isinstance(value.get("verifiability"), str) and value.get("verifiability") in {
            "verifiable", "partially_verifiable", "not_verifiable"
        } else "partially_verifiable",
        "verification_notes": value.get("verification_notes", "")[:500]
        if isinstance(value.get("verification_notes"), str) else "",
        "filters": _sanitize_filters(value.get("filters")),
        "rule_based": True,
    }


def _sanitize_genres(value):
    genres = _string_list(value)
    cleaned = []
    for genre in genres:
        key = normalize_text(genre).replace(",", " ").strip()
        canonical = GENRE_ALIASES.get(key, key)
        if canonical and canonical not in cleaned:
            cleaned.append(canonical)
    return cleaned


def _sanitize_runtime(value):
    if not isinstance(value, dict):
        value = {}
    runtime = {}
    for key in ("min", "max", "target"):
        number = value.get(key)
        runtime[key] = number if isinstance(number, int) and not isinstance(number, bool) and 0 < number <= 600 else None
    return runtime


def _sanitize_filters(value):
    if not isinstance(value, dict):
        value = {}
    year_range = value.get("year_range")
    if not isinstance(year_range, dict):
        year_range = {}
    min_rating = value.get("min_rating")
    if isinstance(min_rating, bool) or not isinstance(min_rating, (int, float)) or not 0 <= min_rating <= 10:
        min_rating = None
    return {
        "genres": _sanitize_genres(value.get("genres")),
        "year_range": {"from": _year_value(year_range.get("from")), "to": _year_value(year_range.get("to"))},
        "min_rating": float(min_rating) if min_rating is not None else None,
        "runtime": _sanitize_runtime(value.get("runtime")),
    }


def local_understanding(raw_query):
    """Interpret a query with the project's deterministic NLP rules."""
    structured = build_structured_query(raw_query)
    preferences = structured["preferences"]
    keywords = list(preferences["free_text_keywords"])
    runtime = preferences["runtime"]
    if any(runtime.get(key) is not None for key in ("min", "max", "target")):
        keywords = [keyword for keyword in keywords if keyword not in WORD_NUMBERS]
    no_twist_ending = bool(re.search(
        r"\b(?:not|no|without|avoid|avoiding)\s+(?:a\s+|any\s+)?twist\b|\bending\s+(?:is\s+)?not\s+(?:a\s+)?twist\b",
        normalize_text(raw_query),
    ))
    if no_twist_ending:
        keywords = [keyword for keyword in keywords if keyword not in {"twist", "twisty"}]
    animal_only = bool(re.search(r"\b(?:only|just)\s+animals?\b", normalize_text(raw_query)))
    silent_request = bool(re.search(r"\bsilent(?:\s+(?:film|movie))?\b", normalize_text(raw_query)))
    black_and_white_request = bool(re.search(r"\bblack\s+and\s+white\b|\bblack-and-white\b", normalize_text(raw_query)))
    concepts = []
    normalized = normalize_text(raw_query)
    required_terms = list(keywords)
    road_trip_request = bool(re.search(r"\broad(?:\s+|-)?trip\b", normalized))
    for item in LOCAL_CONCEPTS:
        if re.search(item["pattern"], normalized, re.IGNORECASE):
            keywords.extend(item["keywords"])
            concepts.extend(item["expanded_concepts"])
            required_terms = []  # a synonym-table concept stands in for the literal words

    decade = re.search(r"\b(\d{2})s\b", normalized)
    if decade:
        century = 1900 if int(decade.group(1)) >= 30 else 2000
        start = century + int(decade.group(1))
        preferences["release_year"] = {"min": start, "max": start + 9}
    year = preferences.get("release_year") or {}
    core_intent = re.sub(
        r"\b(?:please|recommend me|recommend|suggest|show me|i want|i would like|something like|something|a movie|movie|film)\b",
        " ", normalized, flags=re.IGNORECASE,
    )
    core_intent = " ".join(core_intent.split())[:300]
    similar_match = SIMILAR_TO_PATTERN.search(normalized)
    similar_to_title = similar_match.group(1).strip() if similar_match else None
    if similar_to_title:
        required_terms = []
    return {
        "core_intent": core_intent or raw_query[:300],
        "required_terms": required_terms[:6],
        "keywords": list(dict.fromkeys(keywords))[:12],
        "expanded_concepts": list(dict.fromkeys(concepts))[:12],
        "example_titles": [],
        "genres": preferences["genres"],
        "mood_tone": preferences["moods"],
        "year_range": {"from": year.get("min"), "to": year.get("max")},
        "min_rating": None,
        "similar_to": similar_to_title,
        "exclusions": preferences["excluded_genres"] + preferences["excluded_moods"],
        "language_detected": "en",
        "alternative_queries": [],
        "positive_requirements": keywords,
        "negative_requirements": list(dict.fromkeys(
            preferences["excluded_genres"] + preferences["excluded_moods"]
            + preferences.get("excluded_content_descriptors", [])
            + preferences.get("excluded_themes", [])
            + preferences.get("excluded_keywords", [])
            + (["twist ending"] if no_twist_ending else [])
        )),
        "hard_constraints": list(dict.fromkeys(
            ([f"cast constraint: {preferences['cast_gender']}"] if preferences.get("cast_gender") else [])
            + (["release year"] if preferences.get("release_year") else [])
            + (["road trip"] if road_trip_request else [])
            + [f"exclude {item}" for item in (
                preferences["excluded_genres"] + preferences["excluded_moods"]
                + preferences.get("excluded_themes", [])
                + preferences.get("excluded_content_descriptors", [])
            )]
            + [f"exclude keyword {item}" for item in preferences.get("excluded_keywords", [])]
            + (["ending is not a twist"] if no_twist_ending else [])
            + (["only animals"] if animal_only else [])
            + (["silent film"] if silent_request else [])
            + (["black-and-white"] if black_and_white_request else [])
        )),
        "soft_preferences": list(dict.fromkeys(preferences["moods"] + preferences["themes"])),
        "verifiability": "partially_verifiable" if (
            preferences.get("cast_gender") or road_trip_request or no_twist_ending or animal_only or silent_request or black_and_white_request
        ) else "verifiable",
        "verification_notes": "Catalog fields include title, synopsis, genres, keywords, year, rating, and optional TMDB credits.",
        "filters": {
            "genres": preferences["genres"],
            "excluded_genres": preferences["excluded_genres"],
            "excluded_moods": preferences["excluded_moods"],
            "excluded_themes": preferences["excluded_themes"],
            "excluded_content_descriptors": preferences.get("excluded_content_descriptors", []),
            "excluded_keywords": preferences.get("excluded_keywords", []),
            "year_range": {"from": year.get("min"), "to": year.get("max")},
            "min_rating": None,
            "runtime": {
                "min": preferences["runtime"].get("min"),
                "max": preferences["runtime"].get("max"),
                "target": preferences["runtime"].get("target"),
            },
        },
        "rule_based": True,
    }


def interpret_query(raw_query):
    raw_query = raw_query[:MAX_QUERY_LENGTH]
    cache_key = ("understand", raw_query.casefold())
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = local_understanding(raw_query)
    _cache_set(cache_key, result)
    return result


def _movie_text(movie):
    cast = movie.get("cast", movie.get("actors", [])) or []
    cast_text = []
    for person in cast:
        if isinstance(person, dict):
            cast_text.extend((person.get("name", ""), person.get("character", "")))
        elif isinstance(person, str):
            cast_text.append(person)
    fields = [
        movie.get("title", ""), movie.get("original_title", ""),
        movie.get("synopsis", ""), movie.get("overview", ""), movie.get("tagline", ""),
        " ".join(str(genre) for genre in movie.get("genres", [])),
        " ".join(str(theme) for theme in movie.get("themes", [])),
        " ".join(str(mood) for mood in movie.get("mood_tags", [])),
        " ".join(str(keyword) for keyword in movie.get("keywords", [])),
        " ".join(cast_text),
    ]
    return " ".join(field for field in fields if field)


@lru_cache(maxsize=32768)
def _cached_text_tokens(text):
    return tuple(re.findall(r"[a-z0-9]+", normalize_text(str(text)).casefold()))


def _text_tokens(text):
    return list(_cached_text_tokens(str(text)))


def _search_fields(movie):
    title_forms = {
        normalize_phrase(value)
        for value in (movie.get("title", ""), movie.get("original_title", ""))
        if value
    }
    fields = [
        ("title", movie.get("title", "")),
        ("title", movie.get("original_title", "")),
        ("synopsis", movie.get("synopsis", "") or movie.get("overview", "")),
        ("synopsis", movie.get("tagline", "")),
    ]
    for kind, field in (("theme", "themes"), ("keyword", "keywords"), ("genre", "genres")):
        fields.extend(
            (kind, value)
            for value in movie.get(field, []) or []
            if normalize_phrase(value) not in title_forms
        )
    return [(kind, str(value)) for kind, value in fields if value]


def _make_search_vocabulary(movies):
    """Build word and compound support from catalog text and local synonyms."""
    word_support = Counter()
    compound_support = Counter()
    for movie in movies:
        for _, text in _search_fields(movie):
            tokens = _text_tokens(text)
            word_support.update(tokens)
            for size in range(2, min(4, len(tokens)) + 1):
                for start in range(len(tokens) - size + 1):
                    compound_support["".join(tokens[start:start + size])] += 1
    for table in (
        GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS,
        CONTENT_DESCRIPTOR_SYNONYMS,
    ):
        for canonical, forms in table.items():
            for phrase in {canonical, *forms}:
                tokens = _text_tokens(phrase)
                word_support.update(tokens)
                if 2 <= len(tokens) <= 4:
                    compound_support["".join(tokens)] += 1
    return word_support, compound_support


def _split_unknown_word(word, word_support):
    choices = []
    for offset in range(2, len(word) - 1):
        left, right = word[:offset], word[offset:]
        if len(left) < 3 or len(right) < 3 or left not in word_support or right not in word_support:
            continue
        choices.append((-(word_support[left] + word_support[right]), left, right))
    if not choices:
        return [word]
    _, left, right = min(choices)
    return [left, right]


def _normalize_query_concepts(raw_query, understanding, movies):
    required = understanding.get("required_terms") or []
    if required:
        source_terms = required
    else:
        structured_terms = set(understanding.get("genres", [])) | set(understanding.get("mood_tone", []))
        exclusions = _negative_query_terms(understanding)
        source_terms = [
            token for token in _text_tokens(_remove_negated_concepts(raw_query))
            if token not in STOP_WORDS and token not in structured_terms
            and token not in exclusions and token not in EXPLICIT_SYNTAX_TOKENS
            and len(token) > 1
            and not re.fullmatch(r"(?:\d{2,4})s?", token)
        ]
    if not source_terms:
        return {"concepts": [], "terms": [], "variant": "", "variant_applied": False}
    index = _get_bm25_index(movies)
    word_support = index["word_support"]
    compound_support = index["compound_support"]
    terms = []
    variant_applied = False
    for term_index, term in enumerate(source_terms):
        normalized_term = normalize_text(term)
        if "-" in normalized_term:
            variant_applied = True
        for piece in _text_tokens(normalized_term):
            if piece not in word_support:
                split = _split_unknown_word(piece, word_support)
                if len(split) > 1:
                        terms.extend(split)
                        variant_applied = True
                else:
                        closest = max(
                            word_support,
                            key=lambda candidate: (
                                SequenceMatcher(None, piece, candidate).ratio()
                                if abs(len(piece) - len(candidate)) <= 2 else 0.0,
                                candidate,
                            ),
                            default=piece,
                        )
                        if (
                            len(piece) >= 4
                            and len(closest) >= 4
                            and SequenceMatcher(None, piece, closest).ratio() >= 0.78
                        ):
                            neighboring_terms = []
                            if term_index > 0:
                                neighboring_terms.extend(_text_tokens(source_terms[term_index - 1]))
                            if term_index + 1 < len(source_terms):
                                neighboring_terms.extend(_text_tokens(source_terms[term_index + 1]))
                            phrase_supported = any(
                                compound_support.get(neighbor + closest, 0)
                                or compound_support.get(closest + neighbor, 0)
                                for neighbor in neighboring_terms
                            )
                            if closest not in STOP_WORDS or phrase_supported:
                                terms.append(closest)
                                variant_applied = True
                        elif required and closest not in STOP_WORDS:
                            terms.append(piece)
            elif compound_support.get(piece, 0):
                variants = [
                    (word_support.get(left, 0) + word_support.get(right, 0), left, right)
                    for offset in range(2, len(piece) - 1)
                    for left, right in [(piece[:offset], piece[offset:])]
                    if left in word_support and right in word_support
                    and compound_support.get(left + right, 0)
                ]
                if variants:
                    _, left, right = max(variants, key=lambda item: (item[0], item[1], item[2]))
                    terms.extend((left, right))
                    variant_applied = True
                else:
                    terms.append(piece)
            else:
                terms.append(piece)

    concepts = []
    offset = 0
    while offset < len(terms):
        compound = next((
            terms[offset:offset + size]
            for size in range(min(4, len(terms) - offset), 1, -1)
            if compound_support.get("".join(terms[offset:offset + size]), 0)
        ), None)
        if compound:
            concepts.append(compound)
            offset += len(compound)
        else:
            concepts.append([terms[offset]])
            offset += 1
    normalized_terms = list(dict.fromkeys(word for concept in concepts for word in concept))
    original = " ".join(_text_tokens(" ".join(source_terms)))
    normalized = " ".join(normalized_terms)
    variant_applied = variant_applied or original != normalized
    return {
        "concepts": concepts,
        "terms": normalized_terms,
        "variant": normalized if variant_applied else "",
        "variant_applied": variant_applied,
    }


def _direct_query_concepts(normalization, understanding):
    concepts = [list(concept) for concept in normalization.get("concepts", ())]
    seen = {
        CONCEPT_TABLE.get("aliases", {}).get(" ".join(concept), " ".join(concept))
        for concept in concepts
    }
    for preference in understanding.get("soft_preferences", ()):
        phrase = normalize_phrase(
            preference.get("value", "") if isinstance(preference, dict) else preference
        )
        if not phrase:
            continue
        canonical = CONCEPT_TABLE.get("aliases", {}).get(phrase, phrase)
        aliases = CONCEPT_TABLE.get("concepts", {}).get(canonical, {}).get("aliases", ())
        if not any(
            alias.get("category") == "theme"
            for alias in aliases if isinstance(alias, dict)
        ):
            continue
        if canonical in seen:
            continue
        concept = _text_tokens(phrase)
        if concept:
            concepts.append(concept)
            seen.add(canonical)
    return concepts


def _query_evidence(movie, concepts, expansions=()):
    if not concepts and not expansions:
        return {
            "concepts": [], "requirements": [], "score": 0.0,
            "strength": 0.0, "strong": True, "details": [],
            "associated_details": [], "associated_count": 0,
        }
    fields = _search_fields(movie)
    details = []
    scores = []
    all_strong = True
    for concept in concepts:
        label = " ".join(concept)
        matches = []
        for kind, text in fields:
            tokens = _text_tokens(text)
            for start in range(len(tokens) - len(concept) + 1):
                if tokens[start:start + len(concept)] != concept:
                    continue
                exact_tag = kind in {"theme", "keyword"} and tokens == concept
                if kind == "synopsis":
                    strength = 0.9 if len(concept) > 1 else 0.72
                elif exact_tag:
                    strength = 1.0
                elif kind in {"theme", "keyword"} and len(concept) > 1:
                    strength = 0.9
                elif kind in {"theme", "keyword", "genre"}:
                    strength = 0.5
                else:
                    strength = 0.25
                matches.append((
                    strength,
                    kind,
                    kind == "synopsis" or exact_tag or (
                        kind in {"theme", "keyword"} and len(concept) > 1
                    ),
                    label,
                ))
                break
        if not matches and len(concept) > 1:
            anchor = concept[-1]
            for kind, text in fields:
                if anchor not in _text_tokens(text):
                    continue
                strength = (
                    0.55 if kind == "synopsis"
                    else 0.5 if kind in {"theme", "keyword", "genre"}
                    else 0.25
                )
                matches.append((strength, kind, False, anchor))
        if matches:
            strength, source, strong, matched_label = max(
                matches, key=lambda item: (item[0], item[2], item[1])
            )
            scores.append(strength)
            all_strong = all_strong and strong
            details.append({
                "phrase": matched_label, "source": source, "strength": strength, "strong": strong,
            })
        else:
            scores.append(0.0)
            all_strong = False
    associated_details = []
    associated_strengths = []
    associated_terms = set()
    for expansion in expansions:
        phrase = expansion.get("term", "")
        tokens = _text_tokens(phrase)
        if not tokens:
            continue
        matches = []
        for kind, text in fields:
            if kind == "title":
                continue
            text_tokens = _text_tokens(text)
            if any(
                text_tokens[start:start + len(tokens)] == tokens
                for start in range(len(text_tokens) - len(tokens) + 1)
            ):
                field_strength = 0.9 if kind == "synopsis" else 1.0 if (
                    kind in {"theme", "keyword"} and text_tokens == tokens
                ) else 0.65
                matches.append((field_strength, kind))
        if not matches:
            continue
        field_strength, source = max(matches, key=lambda match: (match[0], match[1]))
        association_weight = float(expansion.get("association_weight", 0.0))
        evidence_strength = min(MAX_EXPANSION_WEIGHT, association_weight * field_strength)
        associated_strengths.append(evidence_strength)
        associated_terms.add(phrase)
        associated_details.append({
            "phrase": phrase,
            "source": source,
            "strength": evidence_strength,
            "concept": expansion.get("concept", ""),
        })
    direct_score = sum(scores) / len(scores) if scores else 0.0
    association_score = max(associated_strengths, default=0.0)
    combined_score = max(direct_score, association_score)
    direct_strong = bool(concepts) and all_strong and len(details) == len(concepts)
    return {
        "concepts": details,
        "requirements": [" ".join(concept) for concept in concepts],
        "score": combined_score,
        "strength": max(max(scores, default=0.0), association_score),
        "strong": direct_strong,
        "details": details,
        "associated_details": associated_details,
        "associated_count": len(associated_terms),
    }


def _make_bm25_index(movies):
    postings = defaultdict(list)
    document_lengths = []
    vocabulary = set()
    for position, movie in enumerate(movies):
        frequencies = Counter(simple_word_tokens(_movie_text(movie)))
        document_lengths.append(sum(frequencies.values()))
        vocabulary.update(frequencies)
        for token, frequency in frequencies.items():
            postings[token].append((position, frequency))
    word_support, compound_support = _make_search_vocabulary(movies)
    return {
        "postings": dict(postings),
        "lengths": document_lengths,
        "vocabulary": vocabulary,
        "word_support": word_support,
        "compound_support": compound_support,
        "average_length": sum(document_lengths) / max(1, len(document_lengths)),
        "count": len(movies),
    }


def _get_bm25_index(movies):
    key = id(movies)
    with _BM25_LOCK:
        cached = _BM25_CACHE.get(key)
        if cached is not None and cached[0] is movies:
            return cached[1]
        index = _make_bm25_index(movies)
        if cached is None:
            if len(_BM25_CACHE) >= 2:
                _BM25_CACHE.pop(next(iter(_BM25_CACHE)))
        _BM25_CACHE[key] = (movies, index)
        return index


def _constraint_words(understanding):
    runtime = understanding.get("filters", {}).get("runtime", {})
    words = set(EXPLICIT_SYNTAX_TOKENS)
    if any(runtime.get(key) is not None for key in ("min", "max", "target")):
        words.update(WORD_NUMBERS)
    return words | set(simple_word_tokens(" ".join(words)))


def _query_terms(raw_query, understanding):
    sources = [
        _remove_negated_concepts(raw_query),
        _remove_negated_concepts(understanding.get("core_intent", "")),
        *understanding.get("keywords", []),
        *understanding.get("expanded_concepts", []),
        *understanding.get("example_titles", []),
    ]
    words = simple_word_tokens(" ".join(sources))
    constraint_words = _constraint_words(understanding)
    negative_terms = _negative_query_terms(understanding)
    return list(dict.fromkeys(
        word for word in words
        if word not in STOP_WORDS and word not in constraint_words and word not in negative_terms and len(word) > 1
        and not re.fullmatch(r"(?:\d{2,4})s?", word)
    ))


def _negative_query_terms(understanding):
    phrases = [
        *understanding.get("negative_requirements", []),
        *understanding.get("exclusions", []),
    ]
    filters = understanding.get("filters", {})
    for key in (
        "excluded_genres", "excluded_moods", "excluded_themes",
        "excluded_content_descriptors", "excluded_keywords",
    ):
        phrases.extend(filters.get(key, []))
    return set(simple_word_tokens(" ".join(str(phrase) for phrase in phrases)))


def _remove_negated_concepts(text):
    return re.sub(
        r"\b(?:no|not|without|except|excluding|avoid|avoiding|never)\s+(?:any\s+|a\s+|an\s+|the\s+)?"
        r"(?:woman|women|female|man|men|male|girl|girls|boy|boys|violence|violent|romance|romantic|twist|twisty)\b",
        " ", normalize_text(text),
    )


def _raw_query_terms(raw_query):
    return _raw_query_terms_from_understanding(raw_query, local_understanding(raw_query))


def _raw_query_terms_from_understanding(raw_query, understanding):
    constraint_words = _constraint_words(understanding)
    negative_terms = _negative_query_terms(understanding)
    return list(dict.fromkeys(
        word for word in simple_word_tokens(_remove_negated_concepts(raw_query))
        if word not in STOP_WORDS and word not in constraint_words and word not in negative_terms and len(word) > 1
        and not re.fullmatch(r"(?:\d{2,4})s?", word)
    ))


def _fuzzy_terms(terms, vocabulary):
    expanded = set(terms)
    vocabulary = list(vocabulary)
    vocabulary_set = set(vocabulary)
    for term in set(terms):
        if (
            len(term) < 4
            or term in vocabulary_set
            or term in STOP_WORDS
            or term in EXPLICIT_SYNTAX_TOKENS
            or re.fullmatch(r"\d+(?:\.\d+)?|\d{2,4}s?", term)
        ):
            continue
        closest = None
        closest_ratio = 0.0
        for candidate in vocabulary:
            if abs(len(term) - len(candidate)) > 2:
                continue
            ratio = SequenceMatcher(None, term, candidate).ratio()
            if ratio > closest_ratio:
                closest, closest_ratio = candidate, ratio
        if closest and closest_ratio >= 0.78:
            expanded.add(closest)
    return expanded


def _fuzzy_term_map(terms, vocabulary):
    vocabulary = list(vocabulary)
    vocabulary_set = set(vocabulary)
    mapping = {}
    for term in terms:
        if (
            term in vocabulary_set
            or len(term) < 4
            or term in STOP_WORDS
            or term in EXPLICIT_SYNTAX_TOKENS
            or re.fullmatch(r"\d+(?:\.\d+)?|\d{2,4}s?", term)
        ):
            mapping[term] = term
            continue
        closest = None
        closest_ratio = 0.0
        for candidate in vocabulary:
            if abs(len(term) - len(candidate)) > 2:
                continue
            ratio = SequenceMatcher(None, term, candidate).ratio()
            if ratio > closest_ratio:
                closest, closest_ratio = candidate, ratio
        mapping[term] = closest if closest and closest_ratio >= 0.78 else term
    return mapping


def _bm25_scores(terms, index, weights=None):
    scores = defaultdict(float)
    count = index["count"]
    average_length = index["average_length"] or 1
    for term in terms:
        weight = (weights or {}).get(term, 1.0)
        documents = index["postings"].get(term, ())
        if not documents:
            continue
        document_frequency = len(documents)
        inverse_frequency = math.log(1 + (count - document_frequency + 0.5) / (document_frequency + 0.5))
        for position, frequency in documents:
            length = index["lengths"][position]
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / average_length)
            scores[position] += weight * inverse_frequency * frequency * 2.5 / denominator
    return scores


def _title_scores(understanding, movies):
    hints = list(understanding["example_titles"])
    scores = defaultdict(float)
    for hint in hints:
        normalized_hint = re.sub(r"[^a-z0-9]+", " ", hint.casefold()).strip()
        if not normalized_hint:
            continue
        for position, movie in enumerate(movies):
            title = re.sub(r"[^a-z0-9]+", " ", movie.get("title", "").casefold()).strip()
            if title == normalized_hint:
                scores[position] = max(scores[position], 1.0)
            elif normalized_hint in title:
                scores[position] = max(scores[position], 0.85)
            else:
                ratio = SequenceMatcher(None, normalized_hint, title).ratio()
                if ratio >= 0.74:
                    scores[position] = max(scores[position], ratio * 0.75)
    return scores


def _similar_movie_terms(understanding, movies):
    """Terms from the catalog record the user named with 'like X'; empty if X is not a catalog title."""
    wanted = re.sub(r"[^a-z0-9]+", " ", (understanding.get("similar_to") or "").casefold()).strip()
    if not wanted:
        return []
    for movie in movies:
        if re.sub(r"[^a-z0-9]+", " ", movie.get("title", "").casefold()).strip() == wanted:
            title_words = set(simple_word_tokens(movie["title"]))
            text = " ".join([*movie.get("genres", []), *movie.get("themes", []), *movie.get("keywords", [])])
            return [w for w in dict.fromkeys(simple_word_tokens(text)) if w not in STOP_WORDS and w not in title_words]
    return []


def retrieve_candidates(raw_query, understanding, movies, tfidf_model=None, limit=MAX_CANDIDATES):
    """Rank candidates with local lexical similarity and title/entity hints."""
    if not movies:
        return []
    if understanding.get("similar_to") and tfidf_model:
        source_id = understanding.get("similar_to_movie_id")
        if source_id is None:
            wanted = re.sub(
                r"[^a-z0-9]+", " ",
                (understanding.get("similar_to") or "").casefold(),
            ).strip()
            source = next((
                movie for movie in movies
                if re.sub(r"[^a-z0-9]+", " ", movie.get("title", "").casefold()).strip() == wanted
            ), None)
            source_id = source.get("id") if source else None
        source_vector = tfidf_model.get("vectors", {}).get(source_id)
        if source_vector:
            similar = []
            source_title_key = normalize_title(understanding.get("similar_to") or "")
            for position, movie in enumerate(movies):
                if (
                    movie.get("id") == source_id
                    or normalize_title(movie.get("title", "")) == source_title_key
                ):
                    continue
                movie_vector = tfidf_model.get("vectors", {}).get(movie.get("id"), {})
                score = cosine_similarity(
                    source_vector,
                    movie_vector,
                    tfidf_model.get("norms", {}).get(source_id),
                    tfidf_model.get("norms", {}).get(movie.get("id")),
                )
                if score > 0:
                    similar.append((score, position, movie))
            similar.sort(key=lambda item: (-item[0], item[1], str(item[2].get("id"))))
            return [{
                "movie": movie,
                "search_score": score / similar[0][0] * 100,
                "match_reason": f"TF-IDF metadata similarity to {understanding.get('similar_to')}.",
                "relevance_score": score,
                "query_evidence": {
                    "concepts": [], "score": score / similar[0][0],
                    "strength": score / similar[0][0], "strong": False, "details": [],
                },
                "normalized_terms": [],
                "normalized_query": "",
                "evidence_strength": score / similar[0][0],
                "fallback_only": False,
            } for score, _, movie in similar[:limit]]
    normalization = _normalize_query_concepts(raw_query, understanding, movies)
    concepts = _direct_query_concepts(normalization, understanding)
    base_understanding = dict(understanding)
    base_understanding["expanded_concepts"] = []
    direct_terms = _query_terms(raw_query, base_understanding)
    original_required_terms = set(simple_word_tokens(" ".join(understanding.get("required_terms") or [])))
    direct_terms = [
        term for term in direct_terms
        if term not in original_required_terms
    ]
    direct_terms = list(dict.fromkeys(
        direct_terms
        + normalization["terms"]
        + [word for concept in concepts for word in concept]
        + _similar_movie_terms(understanding, movies)
    ))
    excluded_terms = _negative_query_terms(understanding)
    concept_expansions = expand_query(concepts, understanding, CONCEPT_TABLE, excluded_terms)
    expansion_weights = {}
    for expansion in concept_expansions:
        for word in simple_word_tokens(expansion["term"]):
            if word not in excluded_terms and word not in STOP_WORDS and word not in direct_terms:
                expansion_weights[word] = max(expansion_weights.get(word, 0.0), expansion["weight"])
    synonym_expansions = []
    for phrase in understanding.get("expanded_concepts", ()):
        for word in simple_word_tokens(phrase):
            if word not in excluded_terms and word not in STOP_WORDS and word not in direct_terms:
                expansion_weights[word] = max(expansion_weights.get(word, 0.0), MAX_EXPANSION_WEIGHT)
                synonym_expansions.append({
                    "term": word,
                    "concept": " ".join(concepts[0]) if concepts else "",
                    "association_weight": MAX_EXPANSION_WEIGHT,
                    "source": "hand-written",
                })
    total_expansion_weight = sum(expansion_weights.values())
    if total_expansion_weight > MAX_EXPANSION_WEIGHT:
        scale = MAX_EXPANSION_WEIGHT / total_expansion_weight
        expansion_weights = {
            term: weight * scale for term, weight in expansion_weights.items()
        }
    search_expansions = [
        expansion for expansion in concept_expansions
        if any(word in expansion_weights for word in simple_word_tokens(expansion["term"]))
    ] + synonym_expansions
    term_weights = {term: 1.0 for term in direct_terms}
    for term, weight in expansion_weights.items():
        term_weights[term] = max(term_weights.get(term, 0.0), weight)
    terms = list(term_weights)
    raw_terms = normalization["terms"]
    structured_terms = set(understanding.get("genres", [])) | set(understanding.get("mood_tone", []))
    if (
        not understanding.get("required_terms")
        and not understanding.get("keywords")
        and not understanding.get("expanded_concepts")
        and raw_terms
        and set(raw_terms) <= structured_terms
    ):
        raw_terms = []
    lexical_scores = {}
    if len(movies) <= 5000:
        lexical_index = _get_bm25_index(movies)
        direct_query_terms = sorted(_fuzzy_terms(direct_terms, lexical_index["vocabulary"]))
        weighted_query_terms = {term: 1.0 for term in direct_query_terms}
        for term, weight in sorted(expansion_weights.items()):
            if term in lexical_index["vocabulary"]:
                weighted_query_terms[term] = max(weighted_query_terms.get(term, 0.0), weight)
        lexical_scores = _bm25_scores(weighted_query_terms, lexical_index, weighted_query_terms)
    elif tfidf_model:
        direct_query_terms = sorted(_fuzzy_terms(direct_terms, tfidf_model["idf"].keys()))
        query_vector = vectorize_query(direct_query_terms, tfidf_model["idf"])
        for term, weight in sorted(expansion_weights.items()):
            expanded_vector = vectorize_query([term], tfidf_model["idf"])
            for vector_term, vector_weight in expanded_vector.items():
                query_vector[vector_term] = query_vector.get(vector_term, 0.0) + vector_weight * weight
        query_norm = vector_norm(query_vector) if query_vector else 0.0
        lexical_scores = {
            position: score
            for position, movie in enumerate(movies)
            if (score := cosine_similarity(
                query_vector,
                tfidf_model["vectors"].get(movie["id"], {}),
                query_norm,
                tfidf_model.get("norms", {}).get(movie["id"]),
            )) > 0
        }
    title_scores = _title_scores(understanding, movies)

    rank_lists = []
    if lexical_scores:
        rank_lists.append((sorted(
            lexical_scores,
            key=lambda position: (
                -lexical_scores[position], str(movies[position].get("title", "")).casefold(),
                str(movies[position].get("id", "")),
            ),
        ), 1.0))
    if title_scores:
        rank_lists.append((sorted(
            title_scores,
            key=lambda position: (
                -title_scores[position], str(movies[position].get("title", "")).casefold(),
                str(movies[position].get("id", "")),
            ),
        ), 1.4))

    fused_scores = defaultdict(float)
    for positions, weight in rank_lists:
        for rank, position in enumerate(positions, start=1):
            fused_scores[position] += weight / (60 + rank)
    fallback_only = not fused_scores
    if not fused_scores:
        return []

    evidence_by_position = {
        position: _query_evidence(movies[position], concepts, search_expansions)
        for position in fused_scores
    }
    if concepts:
        qualified = {}
        for position, score in fused_scores.items():
            evidence = evidence_by_position[position]
            matched_count = sum(
                1 for detail in evidence["details"] if detail["strength"] > 0
            )
            if "road trip" in understanding.get("hard_constraints", []):
                searchable_text = normalize_text(_movie_text(movies[position]))
                has_catalog_evidence = bool(re.search(r"\broad(?:\s+|-)?trip\b", searchable_text))
                has_local_hint = title_scores.get(position, 0) >= 0.85
                if not has_catalog_evidence and not has_local_hint:
                    continue
            has_strong_direct = evidence["strong"]
            has_title_phrase = any(
                detail["source"] == "title" and len(_text_tokens(detail["phrase"])) > 1
                for detail in evidence["details"]
            )
            has_non_title_evidence = any(
                detail["source"] != "title" and detail["strength"] > 0
                for detail in evidence["details"]
            )
            has_title_evidence = any(
                detail["source"] == "title" and detail["strength"] > 0
                for detail in evidence["details"]
            )
            has_related_evidence = evidence["associated_count"] >= 2
            if (
                matched_count / len(concepts) >= 0.65
                and (
                    has_strong_direct or has_title_phrase
                    or has_non_title_evidence or has_title_evidence
                )
            ) or has_related_evidence:
                qualified[position] = score
        fused_scores = qualified
        evidence_by_position = {
            position: evidence_by_position[position] for position in fused_scores
        }
        if not fused_scores:
            return []

    ordered = sorted(
        fused_scores,
        key=lambda position: (
            -evidence_by_position.get(position, {}).get("strength", 0.0),
            -fused_scores[position],
            -(movies[position].get("average_rating") or 0),
            -(movies[position].get("vote_count") or 0),
            str(movies[position].get("title", "")).casefold(),
            str(movies[position].get("id", "")),
        ),
    )[:limit]
    max_score = max((fused_scores[position] for position in ordered), default=1.0) or 1.0
    results = []
    for position in ordered:
        movie = movies[position]
        score = fused_scores[position] / max_score * 100
        evidence = evidence_by_position.get(position, {
            "concepts": [], "score": 0.0, "strength": 0.0, "strong": True, "details": [],
        })
        reason = _fallback_reason(movie, terms, understanding, evidence)
        results.append({
            "movie": movie,
            "search_score": score,
            "match_reason": reason,
            "relevance_score": lexical_scores.get(position),
            "query_evidence": evidence,
            "normalized_terms": normalization["terms"],
            "normalized_query": normalization["variant"],
            "evidence_strength": evidence.get("strength", 0.0),
            "fallback_only": fallback_only,
        })
    return results


def retrieve_plain_candidates(raw_query, understanding, movies, tfidf_model=None, limit=MAX_CANDIDATES):
    """Retrieve direct lexical, fuzzy, title, then local TF-IDF fallbacks without expansion."""
    if not movies:
        return []
    direct_understanding = dict(understanding or {})
    direct_understanding["expanded_concepts"] = []
    direct_understanding["keywords"] = []
    terms = _query_terms(raw_query[:MAX_QUERY_LENGTH], direct_understanding)
    if not terms:
        terms = [
            term for term in _text_tokens(_remove_negated_concepts(raw_query[:MAX_QUERY_LENGTH]))
            if term not in STOP_WORDS and term not in EXPLICIT_SYNTAX_TOKENS and len(term) > 1
        ]
    scores = {}
    if terms:
        index = _get_bm25_index(movies)
        direct_terms = sorted(_fuzzy_terms(terms, index["vocabulary"]))
        scores = _bm25_scores(direct_terms, index)
    title_scores = _title_scores(direct_understanding, movies)
    ranked_positions = set(scores) | set(title_scores)
    method = "plain-bm25"
    if not ranked_positions and tfidf_model and terms:
        fuzzy_terms = sorted(_fuzzy_terms(terms, tfidf_model.get("idf", {}).keys()))
        query_vector = vectorize_query(fuzzy_terms, tfidf_model["idf"])
        query_norm = vector_norm(query_vector) if query_vector else 0.0
        scores = {
            position: score
            for position, movie in enumerate(movies)
            if (score := cosine_similarity(
                query_vector,
                tfidf_model.get("vectors", {}).get(movie.get("id"), {}),
                query_norm,
                tfidf_model.get("norms", {}).get(movie.get("id")),
            )) > 0
        }
        ranked_positions = set(scores)
        method = "tfidf-nearest"
    ordered = sorted(
        ranked_positions,
        key=lambda position: (
            -(scores.get(position, 0.0) + title_scores.get(position, 0.0)),
            -(movies[position].get("average_rating") or 0),
            -(movies[position].get("vote_count") or 0),
            str(movies[position].get("title", "")).casefold(),
            str(movies[position].get("id", "")),
        ),
    )[:limit]
    maximum = max(
        (scores.get(position, 0.0) + title_scores.get(position, 0.0) for position in ordered),
        default=1.0,
    ) or 1.0
    return [{
        "movie": movies[position],
        "search_score": (
            scores.get(position, 0.0) + title_scores.get(position, 0.0)
        ) / maximum * 100,
        "match_reason": (
            "Closest available result from direct catalog text."
            if method == "plain-bm25"
            else "Closest available result by local TF-IDF metadata similarity."
        ),
        "relevance_score": scores.get(position),
        "normalized_terms": terms,
        "normalized_query": "",
        "evidence_strength": 0.0,
        "fallback_only": True,
        "fallback_method": method,
    } for position in ordered]


def _fallback_reason(movie, terms, understanding, evidence=None):
    if evidence and evidence.get("details"):
        detail = evidence["details"][0]
        source = {
            "synopsis": "the synopsis",
            "theme": "a theme tag",
            "keyword": "a keyword tag",
            "genre": "a genre tag",
            "title": "the title",
        }.get(detail["source"], "movie metadata")
        return f'Matched "{detail["phrase"]}" in {source}.'
    if evidence and evidence.get("associated_details"):
        detail = evidence["associated_details"][0]
        source = {
            "synopsis": "the synopsis",
            "theme": "a theme tag",
            "keyword": "a keyword tag",
            "genre": "a genre tag",
        }.get(detail["source"], "movie metadata")
        return (
            f'Related evidence includes "{detail["phrase"]}" in {source}, '
            f'associated with "{detail["concept"]}".'
        )
    if any(hint.casefold() in movie.get("title", "").casefold() for hint in understanding["example_titles"]):
        return "Matched a likely title hint from your description."
    searchable = set(simple_word_tokens(_movie_text(movie)))
    if any(term in searchable for term in terms):
        return "The available movie metadata overlaps with the description."
    return "No direct plot evidence was found in the available metadata."


def _basic_assessment(raw_query, candidate, understanding):
    movie = candidate["movie"]
    hard_constraints = understanding.get("hard_constraints", [])
    satisfied = []
    unsatisfied = []
    unverifiable = []
    movie_terms = set(simple_word_tokens(_movie_text(movie)))
    filters = understanding.get("filters", {})
    year_range = filters.get("year_range", {})
    lower_year = year_range.get("from")
    upper_year = year_range.get("to")
    if lower_year is not None or upper_year is not None:
        year = movie.get("release_year")
        if year is not None and (lower_year is None or year >= lower_year) and (upper_year is None or year <= upper_year):
            satisfied.append(f"release year {year}")
        else:
            unsatisfied.append("requested release year")

    title_match_ids = {str(value) for value in understanding.get("title_match_ids", [])}
    if title_match_ids:
        if str(movie.get("id")) in title_match_ids:
            satisfied.append("requested title")
        else:
            unsatisfied.append("requested title")

    genres = {normalize_text(genre) for genre in movie.get("genres", [])}
    for genre in filters.get("genres", []):
        if normalize_text(genre) in genres:
            satisfied.append(f"{genre} genre")
        else:
            unsatisfied.append(f"{genre} genre")

    runtime = filters.get("runtime", {})
    runtime_min, runtime_max = runtime.get("min"), runtime.get("max")
    if runtime_min is not None or runtime_max is not None:
        length = movie.get("runtime") or 0
        if length > 0 and (runtime_min is None or length >= runtime_min) and (runtime_max is None or length <= runtime_max):
            satisfied.append(f"runtime {length} minutes")
        else:
            unsatisfied.append("requested runtime")

    exclusion_preferences = {
        key: filters.get(key, [])
        for key in (
            "excluded_genres", "excluded_moods", "excluded_themes",
            "excluded_content_descriptors", "excluded_keywords",
        )
    }
    if any(exclusion_preferences.values()):
        if is_excluded(movie, exclusion_preferences):
            unsatisfied.append("absence of excluded metadata")
        else:
            satisfied.append("absence of excluded metadata")
    structured_exclusions = {
        normalize_text(value)
        for values in exclusion_preferences.values()
        for value in values
    }
    for constraint in hard_constraints:
        normalized = normalize_text(constraint)
        if normalized.startswith("exclude "):
            term = normalized[len("exclude "):]
            if term.startswith("keyword "):
                term = term[len("keyword "):]
            if term not in structured_exclusions:
                unverifiable.append(f"absence of {term}")
            continue
        if normalized == "release year" or normalized.endswith(" genre"):
            continue
        if "cast constraint" in normalized:
            from .cast_verification import classify_cast_gender
            cast_status = classify_cast_gender(movie)
            if cast_status["status"] == "contradicted":
                unsatisfied.append("cast constraint")
            else:
                unverifiable.append("absence of female characters from the full film")
        elif normalized == "road trip":
            searchable_text = normalize_text(_movie_text(movie))
            if re.search(r"\broad(?:\s+|-)?trip\b", searchable_text):
                satisfied.append("road trip")
            else:
                unverifiable.append("road trip")
        else:
            unverifiable.append(constraint)

    verified_constraint_count = len(satisfied)
    checked_constraint_count = len(satisfied) + len(unsatisfied) + len(unverifiable)
    query_evidence = candidate.get("query_evidence")
    if query_evidence is None:
        normalization = _normalize_query_concepts(raw_query, understanding, [movie])
        query_evidence = _query_evidence(movie, normalization["concepts"])
    requirements = query_evidence.get("requirements", [])
    for detail in query_evidence.get("details", []):
        phrase = detail["phrase"]
        if detail["strong"]:
            source = {
                "synopsis": "synopsis",
                "theme": "theme tag",
                "keyword": "keyword tag",
                "genre": "genre tag",
                "title": "title",
            }.get(detail["source"], "movie metadata")
            evidence_kind = "phrase" if len(phrase.split()) > 1 else "term"
            satisfied.append(f'{phrase} {evidence_kind} in {source}')
        elif detail["strength"]:
            is_phrase_component = any(
                len(requirement.split()) > 1 and phrase in requirement.split()
                for requirement in requirements
            )
            if not is_phrase_component:
                unverifiable.append(f"strong evidence for {phrase}")
    concepts = query_evidence.get("concepts", [])
    matched_phrases = {detail["phrase"] for detail in query_evidence.get("details", [])}
    for phrase in requirements:
        if phrase not in matched_phrases:
            unverifiable.append(f"keyword {phrase}")

    reason_parts = []
    if satisfied:
        reason_parts.append("It meets " + ", ".join(satisfied[:2]))
    weak_evidence = [
        f'{item["phrase"]} in {item["source"]}'
        for item in query_evidence.get("details", [])
        if not item["strong"] and item["strength"]
    ]
    weak_evidence.extend(
        f'{item["phrase"]} in {item["source"]} (associated with {item["concept"]})'
        for item in query_evidence.get("associated_details", [])
    )
    if weak_evidence:
        reason_parts.append(
            ("related evidence is " if not query_evidence.get("details") else "weaker evidence is ")
            + ", ".join(weak_evidence[:2])
        )
    missing = list(dict.fromkeys(unverifiable + unsatisfied))
    if missing:
        reason_parts.append("but does not verify " + ", ".join(missing[:3]))
    reason = "; ".join(reason_parts) + "." if reason_parts else _fallback_reason(
        movie, _query_terms(raw_query, understanding), understanding, query_evidence,
    )
    if not reason.endswith("."):
        reason += "."
    checked = bool(satisfied or unsatisfied or unverifiable)
    if candidate.get("percent") is not None:
        estimated_percent = candidate["percent"]
    elif query_evidence.get("concepts") or query_evidence.get("associated_details"):
        evidence_percent = query_evidence.get("score", 0.0) * 100
        if checked_constraint_count:
            constraint_percent = verified_constraint_count / checked_constraint_count * 100
            estimated_percent = round((evidence_percent + constraint_percent) / 2)
        else:
            estimated_percent = round(evidence_percent)
    elif checked_constraint_count:
        estimated_percent = round(verified_constraint_count / checked_constraint_count * 100)
    else:
        estimated_percent = 0
    required_terms_supported = query_evidence.get("strong", True)
    eligible_for_full = (
        checked and not unsatisfied and not unverifiable and required_terms_supported
        and not candidate.get("fallback_only")
    )
    exact = eligible_for_full and estimated_percent >= EXACT_MATCH_THRESHOLD
    match_tier = (
        "exact" if exact
        else "related" if query_evidence.get("associated_count", 0) >= 2
        and not query_evidence.get("details")
        else "close"
    )
    return {
        "match_level": "full" if exact else "partial",
        "match_tier": match_tier,
        "match_eligible": eligible_for_full,
        "estimated_percent": estimated_percent,
        "evidence_strength": query_evidence.get("strength", 0.0),
        "satisfied": list(dict.fromkeys(satisfied)),
        "unsatisfied": list(dict.fromkeys(unsatisfied)),
        "unverifiable": list(dict.fromkeys(unverifiable)),
        "confidence": min(95, 25 + round(query_evidence.get("score", 0.0) * 55) + 5 * len(satisfied)),
        "match_reason": reason,
        "verification_fallback": bool(
            candidate.get("fallback_only")
            or any(not item.startswith("strong evidence for ") for item in unverifiable)
        ),
    }


def rerank_candidates(raw_query, candidates, understanding=None, on_error=None):
    """Verify and rerank candidates using only available structured metadata."""
    if not candidates:
        return candidates
    try:
        understanding = understanding or local_understanding(raw_query)
    except Exception as error:
        if on_error:
            on_error("verify", error)
        understanding = {}
    cache_key = (
        "verify",
        raw_query.casefold(),
        json.dumps(understanding, sort_keys=True, separators=(",", ":"), default=str),
        tuple(str(item["movie"]["id"]) for item in candidates),
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    verified = []
    for original in candidates[:MAX_CANDIDATES]:
        try:
            item = copy.deepcopy(original)
            item.update(_basic_assessment(raw_query, item, understanding))
        except Exception as error:
            if on_error:
                on_error("verify", error)
            item = copy.deepcopy(original)
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
                "match_reason": "Shown as a closest alternative because evidence could not be verified.",
                "verification_fallback": True,
            })
        item["final_score"] = item["estimated_percent"]
        verified.append(item)
    verified.sort(key=lambda item: (
        item.get("match_level") != "full",
        -item.get("evidence_strength", 0),
        -item.get("confidence", 0),
        -(item["movie"].get("average_rating") or 0),
        -(item["movie"].get("vote_count") or 0),
        str(item["movie"].get("title", "")).casefold(),
        str(item["movie"].get("id", "")),
    ))
    _cache_set(cache_key, verified)
    return verified


def build_movie_embeddings(movies, batch_size=64):
    """Write a local TF-IDF index to the legacy compressed-index path."""
    model = build_tfidf_model(movies)
    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(EMBEDDINGS_PATH, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump({"model": "local-tfidf", **model}, stream, separators=(",", ":"))
    return EMBEDDINGS_PATH