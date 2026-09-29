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


def local_understanding(raw_query):
    """Deterministic query expansion used when no key is configured or API fails."""
    structured = build_structured_query(raw_query)
    preferences = structured["preferences"]
    keywords = list(preferences["free_text_keywords"])
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
    fields = [
        movie.get("title", ""), movie.get("original_title", ""), movie.get("synopsis", ""),
        " ".join(movie.get("genres", [])), " ".join(movie.get("keywords", [])),
        " ".join(movie.get("cast", movie.get("actors", []))),
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
    sources = [raw_query, understanding["core_intent"], *understanding["keywords"],
               *understanding["expanded_concepts"], *understanding["example_titles"]]
    if understanding["similar_to"]:
        sources.append(understanding["similar_to"])
    words = simple_word_tokens(" ".join(sources))
    return [word for word in words if word not in STOP_WORDS]


def _fuzzy_terms(terms, vocabulary):
    expanded = set(terms)
    for term in set(terms):
        if len(term) < 4 or term in vocabulary:
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
    if embeddings:
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
        positions = sorted(
            range(len(movies)),
            key=lambda position: (
                movies[position].get("vote_count", 0),
                movies[position].get("average_rating") or 0,
            ),
            reverse=True,
        )
        fused_scores.update({position: 1 / (60 + rank) for rank, position in enumerate(positions, 1)})

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
        return "Related to " + ", ".join(dict.fromkeys(matched[:3])) + "."
    if any(hint.casefold() in movie.get("title", "").casefold() for hint in understanding["example_titles"]):
        return "Matched a likely title hint from your description."
    if movie.get("vote_count"):
        return "Closest available match by title and movie popularity."
    return "Closest available match to your description."


def rerank_candidates(raw_query, candidates):
    """Ask the model to re-rank one bounded candidate batch; fail open."""
    if not candidates:
        return candidates
    cache_key = ("rerank", raw_query.casefold(), tuple(str(item["movie"]["id"]) for item in candidates))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = {
        "original_query": raw_query[:MAX_QUERY_LENGTH],
        "candidates": [
            {
                "id": item["movie"]["id"],
                "title": item["movie"].get("title", ""),
                "year": item["movie"].get("release_year"),
                "genres": item["movie"].get("genres", []),
                "overview": item["movie"].get("overview") or item["movie"].get("synopsis", ""),
                "keywords": item["movie"].get("keywords", []),
                "cast": item["movie"].get("cast", item["movie"].get("actors", [])),
            }
            for item in candidates[:MAX_CANDIDATES]
        ],
    }
    response = _chat_json(RERANK_PROMPT, payload, max_tokens=1500)
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        return candidates
    by_id = {str(item["movie"]["id"]): item for item in candidates}
    reranked = []
    for row in response["results"][:MAX_CANDIDATES]:
        if not isinstance(row, dict) or str(row.get("id")) not in by_id:
            continue
        score = row.get("relevance_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            continue
        if score < RERANK_MINIMUM_SCORE:
            continue
        item = copy.deepcopy(by_id[str(row["id"])])
        item["relevance_score"] = round(score)
        reason = row.get("reason")
        if isinstance(reason, str) and reason.strip():
            item["match_reason"] = reason.strip()[:220]
        item["final_score"] = score * 0.8 + item["search_score"] * 0.2
        reranked.append(item)
    if not reranked:
        return candidates
    reranked.sort(key=lambda item: item["final_score"], reverse=True)
    _cache_set(cache_key, reranked)
    return reranked


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