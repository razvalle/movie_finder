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
from pathlib import Path

from .normalize import normalize_text
from .query import build_structured_query
from .similarity import cosine_similarity, vector_norm
from .tfidf import build_tfidf_model, simple_word_tokens, vectorize_query


ROOT_DIR = Path(__file__).resolve().parents[1]
EMBEDDINGS_PATH = ROOT_DIR / "data" / "movie_embeddings.json.gz"
MAX_QUERY_LENGTH = 500
MAX_CANDIDATES = 30
CACHE_SECONDS = 600
CACHE_SIZE = 512
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_REDIS_CLIENT = None
_REDIS_URL = None
_REDIS_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)

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
    r"\b(?:similar to|something like|(?:movies?|films?) like|like)\s+([a-z0-9][a-z0-9 :'&-]{1,60}?)"
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
            + (["twist ending"] if no_twist_ending else [])
        )),
        "hard_constraints": list(dict.fromkeys(
            ([f"cast constraint: {preferences['cast_gender']}"] if preferences.get("cast_gender") else [])
            + (["release year"] if preferences.get("release_year") else [])
            + (["road trip"] if road_trip_request else [])
            + [f"exclude {item}" for item in preferences["excluded_genres"]]
            + [f"exclude {item}" for item in preferences.get("excluded_content_descriptors", [])]
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
        " ".join(str(keyword) for keyword in movie.get("keywords", [])),
        " ".join(cast_text),
    ]
    return " ".join(field for field in fields if field)


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
    return {
        "postings": dict(postings),
        "lengths": document_lengths,
        "vocabulary": vocabulary,
        "average_length": sum(document_lengths) / max(1, len(document_lengths)),
        "count": len(movies),
    }


def _get_bm25_index(movies):
    key = id(movies)
    with _BM25_LOCK:
        index = _BM25_CACHE.get(key)
        if index is None:
            index = _make_bm25_index(movies)
            if len(_BM25_CACHE) >= 2:
                _BM25_CACHE.pop(next(iter(_BM25_CACHE)))
            _BM25_CACHE[key] = index
        return index


def _query_terms(raw_query, understanding):
    sources = [_remove_negated_concepts(raw_query), _remove_negated_concepts(understanding["core_intent"]), *understanding["keywords"],
               *understanding["expanded_concepts"], *understanding["example_titles"]]
    words = simple_word_tokens(" ".join(sources))
    return list(dict.fromkeys(
        word for word in words
        if word not in STOP_WORDS and len(word) > 1 and not re.fullmatch(r"(?:19|20)\d{2}", word)
    ))


def _remove_negated_concepts(text):
    return re.sub(
        r"\b(?:no|not|without|except|excluding|avoid|avoiding|never)\s+(?:any\s+|a\s+|an\s+|the\s+)?"
        r"(?:woman|women|female|man|men|male|girl|girls|boy|boys|violence|violent|romance|romantic|twist|twisty)\b",
        " ", normalize_text(text),
    )


def _raw_query_terms(raw_query):
    return list(dict.fromkeys(
        word for word in simple_word_tokens(_remove_negated_concepts(raw_query))
        if word not in STOP_WORDS and len(word) > 1 and not re.fullmatch(r"(?:19|20)\d{2}", word)
    ))


def _fuzzy_terms(terms, vocabulary):
    expanded = set(terms)
    vocabulary = list(vocabulary)
    vocabulary_set = set(vocabulary)
    for term in set(terms):
        if len(term) < 4 or term in vocabulary_set:
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
        if term in vocabulary_set or len(term) < 4:
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


def _bm25_scores(terms, index):
    scores = defaultdict(float)
    count = index["count"]
    average_length = index["average_length"] or 1
    for term in terms:
        documents = index["postings"].get(term, ())
        if not documents:
            continue
        document_frequency = len(documents)
        inverse_frequency = math.log(1 + (count - document_frequency + 0.5) / (document_frequency + 0.5))
        for position, frequency in documents:
            length = index["lengths"][position]
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / average_length)
            scores[position] += inverse_frequency * frequency * 2.5 / denominator
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
    terms = _query_terms(raw_query, understanding)
    terms = list(dict.fromkeys(terms + _similar_movie_terms(understanding, movies)))
    raw_terms = _raw_query_terms(raw_query)
    lexical_scores = {}
    if len(movies) <= 5000:
        lexical_index = _get_bm25_index(movies)
        query_terms = _fuzzy_terms(terms, lexical_index["vocabulary"])
        lexical_scores = _bm25_scores(query_terms, lexical_index)
    elif tfidf_model:
        query_vector = vectorize_query(terms, tfidf_model["idf"])
        query_norm = vector_norm(query_vector) if query_vector else 0.0
        vocabulary = tfidf_model["idf"].keys()
        fuzzy_terms = _fuzzy_terms(terms, vocabulary)
        if fuzzy_terms - set(terms):
            query_vector = vectorize_query(list(fuzzy_terms), tfidf_model["idf"])
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
        rank_lists.append((sorted(lexical_scores, key=lexical_scores.get, reverse=True), 1.0))
    if title_scores:
        rank_lists.append((sorted(title_scores, key=title_scores.get, reverse=True), 1.4))

    fused_scores = defaultdict(float)
    for positions, weight in rank_lists:
        for rank, position in enumerate(positions, start=1):
            fused_scores[position] += weight / (60 + rank)
    fallback_only = not fused_scores
    if not fused_scores:
        return []

    if len(raw_terms) >= 2:
        vocabulary = lexical_index["vocabulary"] if len(movies) <= 5000 else tfidf_model["idf"].keys()
        fuzzy_map = _fuzzy_term_map(raw_terms, vocabulary)
        qualified = {}
        required = [fuzzy_map.get(term, term) for term in understanding.get("required_terms") or []]
        for position, score in fused_scores.items():
            movie_terms = set(simple_word_tokens(_movie_text(movies[position])))
            if required and title_scores.get(position, 0) < 0.85 and not all(term in movie_terms for term in required):
                continue
            matched_count = sum(1 for term in raw_terms if fuzzy_map[term] in movie_terms)
            if "road trip" in understanding.get("hard_constraints", []):
                searchable_text = normalize_text(_movie_text(movies[position]))
                has_catalog_evidence = bool(re.search(r"\broad(?:\s+|-)?trip\b", searchable_text))
                has_local_hint = title_scores.get(position, 0) >= 0.85
                if not has_catalog_evidence and not has_local_hint:
                    continue
            if title_scores.get(position, 0) >= 0.85 or matched_count / len(raw_terms) >= 0.65:
                qualified[position] = score
        fused_scores = qualified
        if not fused_scores:
            return []

    ordered = sorted(fused_scores, key=fused_scores.get, reverse=True)[:limit]
    max_score = max((fused_scores[position] for position in ordered), default=1.0) or 1.0
    results = []
    for position in ordered:
        movie = movies[position]
        score = fused_scores[position] / max_score * 100
        reason = _fallback_reason(movie, terms, understanding)
        results.append({
            "movie": movie,
            "search_score": score,
            "match_reason": reason,
            "relevance_score": None,
            "fallback_only": fallback_only,
        })
    return results


def _fallback_reason(movie, terms, understanding):
    searchable = set(simple_word_tokens(_movie_text(movie)))
    matched = [term for term in terms if term in searchable]
    if matched:
        return "The available synopsis or metadata overlaps with the description."
    if any(hint.casefold() in movie.get("title", "").casefold() for hint in understanding["example_titles"]):
        return "Matched a likely title hint from your description."
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

    descriptors = {normalize_text(value) for value in movie.get("content_descriptors", [])}
    for constraint in hard_constraints:
        normalized = normalize_text(constraint)
        if normalized == "release year" or normalized.endswith(" genre"):
            continue
        if normalized.startswith("exclude "):
            excluded = normalized.removeprefix("exclude ")
            if excluded in genres or excluded in descriptors:
                unsatisfied.append(f"absence of {excluded}")
            else:
                unverifiable.append(f"absence of {excluded}")
        elif "cast constraint" in normalized:
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

    query_terms = _query_terms(raw_query, understanding)
    matched = [term for term in query_terms if term in movie_terms and term not in STOP_WORDS]
    for term in understanding.get("required_terms", []):
        if term in movie_terms:
            satisfied.append(f"keyword {term}")
        else:
            unverifiable.append(f"keyword {term}")
    reason_parts = []
    if satisfied:
        reason_parts.append("It meets " + ", ".join(satisfied[:2]))
    if matched:
        reason_parts.append("the available metadata mentions " + ", ".join(dict.fromkeys(matched[:3])))
    missing = list(dict.fromkeys(unverifiable + unsatisfied))
    if missing:
        reason_parts.append("but does not verify " + ", ".join(missing[:3]))
    reason = "; ".join(reason_parts) + "." if reason_parts else _fallback_reason(movie, query_terms, understanding)
    if not reason.endswith("."):
        reason += "."
    # Overlap terms are retrieval evidence, not checked constraints.
    checked = bool(satisfied or unsatisfied or unverifiable)
    exact = checked and not unsatisfied and not unverifiable
    return {
        "match_level": "full" if exact else "partial",
        "satisfied": list(dict.fromkeys(satisfied + ([f"metadata overlap: {term}" for term in matched[:3]]))),
        "unsatisfied": list(dict.fromkeys(unsatisfied)),
        "unverifiable": list(dict.fromkeys(unverifiable)),
        "confidence": min(70, 35 + 8 * len(matched) + 5 * len(satisfied)),
        "match_reason": reason,
        "verification_fallback": bool(candidate.get("fallback_only") or understanding.get("required_terms") and any(
            term not in movie_terms for term in understanding["required_terms"]
        )),
    }


def rerank_candidates(raw_query, candidates, understanding=None):
    """Verify and rerank candidates using only available structured metadata."""
    if not candidates:
        return candidates
    understanding = understanding or local_understanding(raw_query)
    cache_key = ("verify", raw_query.casefold(), tuple(str(item["movie"]["id"]) for item in candidates))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    verified = []
    for original in candidates[:MAX_CANDIDATES]:
        item = copy.deepcopy(original)
        item.update(_basic_assessment(raw_query, item, understanding))
        item["final_score"] = item["confidence"] * 0.8 + item["search_score"] * 0.2
        verified.append(item)
    verified.sort(key=lambda item: (
        item.get("match_level") == "full",
        len(item.get("satisfied", [])),
        item.get("confidence", 0),
        item["movie"].get("average_rating") or 0,
        item["movie"].get("vote_count", 0),
    ), reverse=True)
    _cache_set(cache_key, verified)
    return verified


def build_movie_embeddings(movies, batch_size=64):
    """Write a local TF-IDF index to the legacy compressed-index path."""
    model = build_tfidf_model(movies)
    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(EMBEDDINGS_PATH, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump({"model": "local-tfidf", **model}, stream, separators=(",", ":"))
    return EMBEDDINGS_PATH