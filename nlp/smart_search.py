"""LLM-assisted query understanding and lightweight hybrid retrieval."""

import copy
import gzip
import json
import math
import os
import re
import threading
import time
from collections import Counter, OrderedDict, defaultdict, deque
from difflib import SequenceMatcher
from pathlib import Path
from urllib.request import Request, urlopen

from .extract import extract_preferences
from .normalize import normalize_text
from .query import build_structured_query
from .similarity import cosine_similarity, vector_norm
from .tfidf import simple_word_tokens, vectorize_query


ROOT_DIR = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT_DIR / "prompts"
EMBEDDINGS_PATH = ROOT_DIR / "data" / "movie_embeddings.json.gz"
HTTP_TIMEOUT_SECONDS = 2.2
MAX_QUERY_LENGTH = 500
MAX_CANDIDATES = 30
RERANK_MINIMUM_SCORE = 18
CACHE_SECONDS = 600
CACHE_SIZE = 512
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60

QUERY_PROMPT = (PROMPTS_DIR / "query_understanding_system.txt").read_text(encoding="utf-8")
RERANK_PROMPT = (PROMPTS_DIR / "rerank_system.txt").read_text(encoding="utf-8")

_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_RATE_LIMITS = defaultdict(deque)
_RATE_LIMIT_LOCK = threading.Lock()
_BM25_CACHE = {}
_BM25_LOCK = threading.Lock()
_EMBEDDINGS = None
_EMBEDDINGS_LOCK = threading.Lock()

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
        "example_titles": ["The Lord of the Rings: The Fellowship of the Ring", "The Hobbit: An Unexpected Journey"],
    },
    {
        "pattern": r"\brobot\b.*\b(?:love|romance|falls for|falls in love)\b|\b(?:love|romance)\b.*\brobot\b",
        "keywords": ["robot", "artificial intelligence", "romance"],
        "expanded_concepts": ["android", "human-machine relationship", "science fiction"],
        "example_titles": ["WALL-E", "Her", "The Wild Robot"],
    },
    {
        "pattern": r"\b(?:road trip|roadtrip)\b",
        "keywords": ["road trip", "journey", "travel"],
        "expanded_concepts": ["comedy", "friends", "cross-country trip"],
        "example_titles": ["Dumb and Dumber", "Little Miss Sunshine", "Thelma & Louise"],
    },
    {
        "pattern": r"\b(?:like|similar to)\s+inception\b",
        "keywords": ["dream", "reality", "mind-bending"],
        "expanded_concepts": ["layered reality", "memory", "nonlinear science fiction"],
        "example_titles": ["Inception", "The Prestige", "Memento", "Tenet"],
    },
    {
        "pattern": r"\b(?:same day|over and over|repeats? the same day|time loop|relives?)\b",
        "keywords": ["time loop", "repeating day", "reliving the same day"],
        "expanded_concepts": ["time loop", "temporal repetition", "resetting timeline"],
        "example_titles": ["Groundhog Day", "Palm Springs", "Edge of Tomorrow"],
    },
    {
        "pattern": r"\b(?:space|spaceship|outer space)\b.*\b(?:scary|horror|terrifying|alien)\b|\b(?:scary|horror)\b.*\b(?:space|spaceship|outer space)\b",
        "keywords": ["space horror", "alien", "spaceship"],
        "expanded_concepts": ["deep space", "isolated crew", "extraterrestrial threat"],
        "example_titles": ["Alien", "Event Horizon", "The Thing"],
    },
    {
        "pattern": r"\b(?:rat|mouse)\b.*\b(?:cook|chef|cooking)\b|\b(?:cook|chef|cooking)\b.*\b(?:rat|mouse)\b",
        "keywords": ["rat chef", "cooking", "restaurant"],
        "expanded_concepts": ["animated culinary story", "Paris kitchen", "ambitious chef"],
        "example_titles": ["Ratatouille"],
    },
    {
        "pattern": r"\bchess\b.*\b(?:prodigy|genius|young|child|player)\b|\b(?:prodigy|genius)\b.*\bchess\b",
        "keywords": ["chess", "prodigy", "young chess player"],
        "expanded_concepts": ["tournament", "chess master", "coming of age"],
        "example_titles": ["Searching for Bobby Fischer", "Queen of Katwe"],
    },
    {
        "pattern": r"\b(?:dragon|dragons|drgons)\b",
        "keywords": ["dragon", "dragons"],
        "expanded_concepts": ["fantasy", "mythical creature", "fire-breathing"],
        "example_titles": ["How to Train Your Dragon", "The Hobbit: An Unexpected Journey"],
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
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + CACHE_SECONDS, copy.deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


def allow_search(client_id):
    """Allow a bounded number of search requests per client per minute."""
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        events = _RATE_LIMITS[client_id]
        while events and now - events[0] > RATE_LIMIT_WINDOW_SECONDS:
            events.popleft()
        if len(events) >= RATE_LIMIT_REQUESTS:
            return False
        events.append(now)
        return True


def _chat_json(system_prompt, payload, max_tokens=1100):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    body = json.dumps({
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }).encode("utf-8")
    request = Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        content = response_data["choices"][0]["message"]["content"]
        result = json.loads(content)
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _string_list(value, limit=12, length=100):
    if not isinstance(value, list):
        return []
    return [item.strip()[:length] for item in value if isinstance(item, str) and item.strip()][:limit]


def _year_value(value):
    if isinstance(value, int) and not isinstance(value, bool) and 1880 <= value <= 2100:
        return value
    return None


def sanitize_understanding(value, raw_query):
    """Validate model output and fill every optional field with a safe default."""
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
        "llm_used": True,
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
    }


def local_understanding(raw_query):
    """Deterministic query expansion used when no key is configured or API fails."""
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
    example_titles = []
    normalized = normalize_text(raw_query)
    for item in LOCAL_CONCEPTS:
        if re.search(item["pattern"], normalized, re.IGNORECASE):
            keywords.extend(item["keywords"])
            concepts.extend(item["expanded_concepts"])
            example_titles.extend(item["example_titles"])

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
    return {
        "core_intent": core_intent or raw_query[:300],
        "keywords": list(dict.fromkeys(keywords))[:12],
        "expanded_concepts": list(dict.fromkeys(concepts))[:12],
        "example_titles": list(dict.fromkeys(example_titles))[:12],
        "genres": preferences["genres"],
        "mood_tone": preferences["moods"],
        "year_range": {"from": year.get("min"), "to": year.get("max")},
        "min_rating": None,
        "similar_to": "Inception" if re.search(r"\blike inception\b", normalized) else None,
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
            + [f"exclude {item}" for item in preferences["excluded_genres"]]
            + [f"exclude {item}" for item in preferences.get("excluded_content_descriptors", [])]
            + (["ending is not a twist"] if no_twist_ending else [])
            + (["only animals"] if animal_only else [])
            + (["silent film"] if silent_request else [])
            + (["black-and-white"] if black_and_white_request else [])
        )),
        "soft_preferences": list(dict.fromkeys(preferences["moods"] + preferences["themes"])),
        "verifiability": "partially_verifiable" if (
            preferences.get("cast_gender") or no_twist_ending or animal_only or silent_request or black_and_white_request
        ) else "verifiable",
        "verification_notes": "Catalog fields include title, synopsis, genres, keywords, year, rating, and optional TMDB credits.",
        "filters": {
            "genres": preferences["genres"],
            "year_range": {"from": year.get("min"), "to": year.get("max")},
            "min_rating": None,
        },
        "llm_used": False,
    }


def understand_query(raw_query):
    raw_query = raw_query[:MAX_QUERY_LENGTH]
    cache_key = ("understand", raw_query.casefold())
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    fallback = local_understanding(raw_query)
    response = _chat_json(QUERY_PROMPT, {"raw_query": raw_query}, max_tokens=800)
    result = sanitize_understanding(response, raw_query) if response else fallback
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
    if understanding["similar_to"]:
        sources.append(understanding["similar_to"])
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


def _load_embeddings():
    global _EMBEDDINGS
    if _EMBEDDINGS is not None:
        return _EMBEDDINGS
    with _EMBEDDINGS_LOCK:
        if _EMBEDDINGS is not None:
            return _EMBEDDINGS
        try:
            with gzip.open(EMBEDDINGS_PATH, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            expected_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
            if payload.get("model") == expected_model and isinstance(payload.get("vectors"), dict):
                _EMBEDDINGS = payload["vectors"]
            else:
                _EMBEDDINGS = {}
        except (OSError, ValueError, TypeError):
            _EMBEDDINGS = {}
    return _EMBEDDINGS


def _embedding_for_query(text):
    if not os.getenv("OPENAI_API_KEY"):
        return None
    cache_key = ("embedding", os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"), text.casefold())
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    body = json.dumps({
        "model": os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "input": text[:MAX_QUERY_LENGTH],
    }).encode("utf-8")
    request = Request(
        "https://api.openai.com/v1/embeddings",
        data=body,
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
        vector = payload["data"][0]["embedding"]
        if isinstance(vector, list) and vector and all(isinstance(value, (int, float)) for value in vector):
            _cache_set(cache_key, vector)
            return vector
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def _cosine(left, right):
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _title_scores(understanding, movies):
    hints = list(understanding["example_titles"])
    if understanding["similar_to"]:
        hints.append(understanding["similar_to"])
    scores = defaultdict(float)
    for hint in hints:
        normalized_hint = re.sub(r"[^a-z0-9]+", " ", hint.casefold()).strip()
        if not normalized_hint:
            continue
        for position, movie in enumerate(movies):
            title = re.sub(r"[^a-z0-9]+", " ", movie.get("title", "").casefold()).strip()
            if title == normalized_hint:
                scores[position] = max(scores[position], 1.0)
            elif normalized_hint in title or title in normalized_hint:
                scores[position] = max(scores[position], 0.85)
            else:
                ratio = SequenceMatcher(None, normalized_hint, title).ratio()
                if ratio >= 0.74:
                    scores[position] = max(scores[position], ratio * 0.75)
    return scores


def retrieve_candidates(raw_query, understanding, movies, tfidf_model=None, limit=MAX_CANDIDATES):
    """Merge BM25, optional cosine embeddings, and title/entity hints with RRF."""
    if not movies:
        return []
    terms = _query_terms(raw_query, understanding)
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

    embeddings = _load_embeddings()
    query_embedding = None
    semantic_scores = {}
    if embeddings and raw_terms:
        query_embedding = _embedding_for_query(
            " ".join([understanding["core_intent"], *understanding["expanded_concepts"]])
        )
        if query_embedding:
            for position, movie in enumerate(movies):
                vector = embeddings.get(str(movie["id"]))
                if vector:
                    semantic_scores[position] = _cosine(query_embedding, vector)

    rank_lists = []
    if lexical_scores:
        rank_lists.append((sorted(lexical_scores, key=lexical_scores.get, reverse=True), 1.0))
    if semantic_scores:
        rank_lists.append((sorted(semantic_scores, key=semantic_scores.get, reverse=True), 1.15))
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
        for position, score in fused_scores.items():
            movie_terms = set(simple_word_tokens(_movie_text(movies[position])))
            matched_count = sum(1 for term in raw_terms if fuzzy_map[term] in movie_terms)
            if matched_count / len(raw_terms) >= 0.65:
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


def _validated_reason(value):
    if not isinstance(value, str):
        return "Closest match based on the available movie details."
    reason = value.strip()[:220]
    if not reason or re.match(r"^(?:related to|matches? the (?:word|token)|because (?:the )?title contains)\b", reason, re.I):
        return "Closest match based on the available movie details."
    return reason


def _basic_assessment(raw_query, candidate, understanding):
    movie = candidate["movie"]
    hard_constraints = understanding.get("hard_constraints", [])
    satisfied = []
    unsatisfied = []
    unverifiable = []
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
        else:
            unverifiable.append(constraint)

    movie_terms = set(simple_word_tokens(_movie_text(movie)))
    query_terms = _query_terms(raw_query, understanding)
    matched = [term for term in query_terms if term in movie_terms and term not in STOP_WORDS]
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
    return {
        "match_level": "partial",
        "satisfied": list(dict.fromkeys(satisfied + ([f"metadata overlap: {term}" for term in matched[:3]]))),
        "unsatisfied": list(dict.fromkeys(unsatisfied)),
        "unverifiable": list(dict.fromkeys(unverifiable)),
        "confidence": min(70, 35 + 8 * len(matched) + 5 * len(satisfied)),
        "match_reason": reason,
        "verification_fallback": True,
    }


def rerank_candidates(raw_query, candidates, understanding=None):
    """Verify a bounded candidate batch against supplied metadata; fail closed to partial."""
    if not candidates:
        return candidates
    understanding = understanding or local_understanding(raw_query)
    cache_key = ("verify", raw_query.casefold(), tuple(str(item["movie"]["id"]) for item in candidates))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = {
        "original_query": raw_query[:MAX_QUERY_LENGTH],
        "parsed_requirements": {
            key: understanding.get(key)
            for key in ("positive_requirements", "negative_requirements", "hard_constraints", "soft_preferences", "verifiability", "verification_notes")
        },
        "candidates": [
            {
                "id": item["movie"]["id"],
                "title": item["movie"].get("title", ""),
                "year": item["movie"].get("release_year"),
                "runtime": item["movie"].get("runtime"),
                "genres": item["movie"].get("genres", []),
                "overview": item["movie"].get("overview") or item["movie"].get("synopsis", ""),
                "tagline": item["movie"].get("tagline", ""),
                "keywords": item["movie"].get("keywords", []),
                "cast": item["movie"].get("cast", item["movie"].get("actors", [])),
                "content_descriptors": item["movie"].get("content_descriptors", []),
                "top_cast_size": item["movie"].get("top_cast_size"),
                "gender_coverage": item["movie"].get("gender_coverage"),
                "certification": item["movie"].get("certification"),
            }
            for item in candidates[:MAX_CANDIDATES]
        ],
    }
    response = _chat_json(RERANK_PROMPT, payload, max_tokens=1800)
    by_id = {str(item["movie"]["id"]): item for item in candidates}
    response_rows = response.get("results") if isinstance(response, dict) else None
    rows_by_id = {
        str(row.get("id")): row for row in response_rows or []
        if isinstance(row, dict) and str(row.get("id")) in by_id
    }
    valid_response = (
        isinstance(response_rows, list)
        and len(rows_by_id) == len(by_id)
        and all(
            row.get("match_level") in {"full", "partial", "none"}
            and isinstance(row.get("confidence"), int)
            and not isinstance(row.get("confidence"), bool)
            and 0 <= row["confidence"] <= 100
            and isinstance(row.get("satisfied"), list)
            and isinstance(row.get("unsatisfied"), list)
            and isinstance(row.get("unverifiable"), list)
            and isinstance(row.get("reason"), str)
            for row in rows_by_id.values()
        )
    )
    if not valid_response:
        rows_by_id = {}
    verified = []
    for identifier, original in by_id.items():
        row = rows_by_id.get(identifier)
        if not row:
            if valid_response:
                continue
            item = copy.deepcopy(original)
            item.update(_basic_assessment(raw_query, item, understanding))
            verified.append(item)
            continue
        match_level = row.get("match_level")
        confidence = row.get("confidence")
        if match_level not in {"full", "partial", "none"}:
            continue
        if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
            continue
        satisfied = _string_list(row.get("satisfied"), limit=10)
        unsatisfied = _string_list(row.get("unsatisfied"), limit=10)
        unverifiable = _string_list(row.get("unverifiable"), limit=10)
        hard_constraints = understanding.get("hard_constraints", [])
        if (
            understanding.get("verifiability") != "verifiable"
            or unverifiable
            or unsatisfied
            or len(satisfied) < len(hard_constraints)
        ):
            if match_level == "full":
                match_level = "partial"
        unprovable_negative = re.search(
            r"\b(?:no|not|without|avoid|excluding|except)\b.{0,35}\b(?:women?|female|men?|male|violence|violent|romance|romantic|twist)\b",
            raw_query,
            re.IGNORECASE,
        )
        if unprovable_negative and match_level == "full":
            match_level = "partial"
        if match_level == "none" or confidence < RERANK_MINIMUM_SCORE:
            continue
        reason = row.get("reason")
        item = copy.deepcopy(original)
        item.update({
            "match_level": match_level,
            "satisfied": satisfied,
            "unsatisfied": unsatisfied,
            "unverifiable": unverifiable,
            "confidence": confidence,
            "match_reason": _validated_reason(reason),
            "verification_fallback": False,
            "final_score": confidence * 0.8 + item["search_score"] * 0.2,
        })
        verified.append(item)
    if not verified and not valid_response:
        return []
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
    """Create and write the optional compressed OpenAI embedding index."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY before building movie embeddings.")
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    vectors = {}
    for start in range(0, len(movies), batch_size):
        batch = movies[start:start + batch_size]
        body = json.dumps({
            "model": model,
            "input": [_movie_text(movie)[:6000] for movie in batch],
        }).encode("utf-8")
        request = Request(
            "https://api.openai.com/v1/embeddings",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for movie, item in zip(batch, payload["data"]):
            vectors[str(movie["id"])] = item["embedding"]
        print(f"Indexed {min(start + len(batch), len(movies)):,}/{len(movies):,} movies")

    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(EMBEDDINGS_PATH, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump({"model": model, "vectors": vectors}, stream, separators=(",", ":"))
    return EMBEDDINGS_PATH