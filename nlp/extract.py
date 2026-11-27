"""
nlp/extract.py
-----------------------------------------------------------------------
STEPS 4-9 of the pipeline, combined: PREFERENCE EXTRACTION

Orchestrates the smaller single-purpose modules (normalize, tokenize,
negation, runtime, release_year) and the synonym dictionary to turn raw
user text into a structured preference dict:

{
  "raw_input": str, "normalized_text": str, "tokens": [...],
  "moods": [...],            "genres": [...],            "themes": [...],
  "excluded_moods": [...],   "excluded_genres": [...],    "excluded_themes": [...],
  "runtime": {"min", "max", "target", "tolerance"},
  "release_year": {"min", "max"} | None,
  "free_text_keywords": [...],   # leftover meaningful words for TF-IDF fallback
  "negation_scopes": [(int, int), ...]  # kept for debugging/tests
}
-----------------------------------------------------------------------
"""

import math
import copy
from collections import defaultdict
from functools import lru_cache

from .normalize import normalize_text
from .tokenize import STOPWORDS, tokenize, build_ngrams, remove_stopwords
from .fuzzy import correct_unknown_token
from .negation import (
    NEGATION_CUES,
    find_negation_scopes,
    find_soft_negation_scopes,
    is_retroactively_negated,
    is_within_negation_scope,
)
from .runtime import WORD_NUMBERS, extract_runtime_constraint, extract_vague_runtime_constraint
from .release_year import extract_release_year_constraint
from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
    build_reverse_index,
)

REVERSE_INDEX = build_reverse_index()  # built once at module import
NEGATION_SYNTAX_TOKENS = {
    "above", "at", "below", "between", "from", "hour", "hours", "hr", "hrs",
    "least", "less", "longer", "max", "maximum", "min", "minimum", "mins",
    "minute", "minutes", "more", "over", "shorter", "than", "under", "within",
}
NEGATION_CUE_WORDS = NEGATION_CUES | {"but"}
_SYNONYM_TABLES = (
    ("genre", GENRE_SYNONYMS),
    ("mood", MOOD_SYNONYMS),
    ("theme", THEME_SYNONYMS),
    ("content_descriptor", CONTENT_DESCRIPTOR_SYNONYMS),
)


@lru_cache(maxsize=1)
def _catalog_word_document_frequency():
    from data.movies import MOVIES

    document_frequency = {}
    for movie in MOVIES:
        movie_words = set()
        for field in (
            "title", "original_title", "synopsis", "overview", "tagline",
            "genres", "themes", "mood_tags", "keywords", "content_descriptors",
        ):
            value = movie.get(field)
            values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            for item in values:
                if isinstance(item, str):
                    movie_words.update(
                        token for token in tokenize(normalize_text(item))
                        if token != ","
                    )
        for word in movie_words:
            document_frequency[word] = document_frequency.get(word, 0) + 1
    return tuple(sorted(document_frequency.items()))


@lru_cache(maxsize=1)
def _catalog_word_vocabulary():
    return frozenset(word for word, _ in _catalog_word_document_frequency())


def _high_entropy_terms(movies):
    genre_distributions = defaultdict(lambda: defaultdict(int))
    for movie in movies:
        genres = {
            normalize_text(genre)
            for genre in movie.get("genres", [])
            if isinstance(genre, str) and normalize_text(genre)
        }
        if not genres:
            continue
        document_words = set()
        for field in (
            "title", "original_title", "synopsis", "overview", "tagline",
            "genres", "themes", "mood_tags", "keywords", "content_descriptors",
        ):
            value = movie.get(field)
            values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            for item in values:
                if isinstance(item, str):
                    document_words.update(
                        token for token in tokenize(normalize_text(item))
                        if token not in STOPWORDS
                    )
        for word in document_words:
            for genre in genres:
                genre_distributions[word][genre] += 1

    ambiguous = set()
    for word, distribution in genre_distributions.items():
        total = sum(distribution.values())
        if total < 5 or len(distribution) < 3:
            continue
        entropy = -sum(
            (count / total) * math.log(count / total)
            for count in distribution.values()
        )
        normalized_entropy = entropy / math.log(len(distribution))
        if normalized_entropy >= 0.7:
            ambiguous.add(word)
    return frozenset(ambiguous)


@lru_cache(maxsize=1)
def _synonym_category_index():
    mappings = defaultdict(lambda: defaultdict(set))
    for category, table in _SYNONYM_TABLES:
        for canonical, forms in table.items():
            for form in (canonical, *forms):
                phrase = " ".join(tokenize(normalize_text(form)))
                if phrase:
                    mappings[phrase][category].add(canonical)
    return {
        phrase: {
            category: frozenset(tags)
            for category, tags in categories.items()
        }
        for phrase, categories in mappings.items()
    }


@lru_cache(maxsize=1)
def _catalog_ambiguous_terms():
    from data.movies import MOVIES

    synonym_index = _synonym_category_index()
    ambiguous_synonyms = {
        phrase for phrase, categories in synonym_index.items()
        if len(categories) > 1
        or any(len(tags) > 1 for tags in categories.values())
    }
    return frozenset(ambiguous_synonyms | _high_entropy_terms(MOVIES))


def _field_category_hint(tokens, start, end):
    field_terms = {"genre": "genre", "genres": "genre", "mood": "mood",
                   "moods": "mood", "theme": "theme", "themes": "theme"}
    nearby = tokens[max(0, start - 3):start] + tokens[end + 1:end + 4]
    return next(
        (field_terms[token] for token in reversed(nearby) if token in field_terms),
        None,
    )


def _has_second_signal(ngram, tokens, ngrams, ambiguous_terms):
    start, end = ngram["start_index"], ngram["end_index"]
    for other in ngrams:
        other_start, other_end = other["start_index"], other["end_index"]
        if other_start == start and other_end == end:
            continue
        adjacent = other_end + 1 == start or end + 1 == other_start
        if adjacent and REVERSE_INDEX.get(other["phrase"]):
            return True
    for direction, boundary in ((-1, start), (1, end)):
        for distance in range(1, 4):
            position = boundary + direction * distance
            if position < 0 or position >= len(tokens):
                break
            if distance > 1:
                bridge = tokens[position + 1:boundary] if direction < 0 else tokens[boundary + 1:position]
                if not all(
                    word in STOPWORDS or word in NEGATION_CUE_WORDS
                    for word in bridge
                ):
                    break
            word = tokens[position]
            is_movie_noun = word in {"movie", "movies", "film", "films"}
            has_negation_bridge = any(
                item in NEGATION_CUE_WORDS
                for item in (
                    tokens[position + 1:boundary]
                    if direction < 0 else tokens[boundary + 1:position]
                )
            )
            if is_movie_noun:
                return True
            if has_negation_bridge:
                continue
            if REVERSE_INDEX.get(word):
                return True
            if (
                word not in STOPWORDS
                and word not in NEGATION_CUE_WORDS
                and word not in NEGATION_SYNTAX_TOKENS
                and word not in WORD_NUMBERS
                and not word.isdigit()
                and word not in ambiguous_terms
            ):
                return True
    return False


def _field_specific_hit(phrase, category):
    tags = _synonym_category_index().get(phrase, {}).get(category, ())
    return {"category": category, "tag": next(iter(tags))} if len(tags) == 1 else None


def _confirmed_query_categories(tokens, ngrams, ambiguous_terms):
    categories = set()
    for ngram in ngrams:
        hit = REVERSE_INDEX.get(ngram["phrase"])
        if not hit:
            continue
        if ngram["phrase"] not in ambiguous_terms:
            categories.add(hit["category"])
            continue
        field_hint = _field_category_hint(
            tokens, ngram["start_index"], ngram["end_index"],
        )
        if field_hint and _field_specific_hit(ngram["phrase"], field_hint):
            categories.add(field_hint)
        elif _has_second_signal(ngram, tokens, ngrams, ambiguous_terms):
            categories.add(hit["category"])
    return categories


def fuzzy_synonym_match(phrase):
    """Recover a likely dictionary word from a short user typo.

    Fuzzy matching is limited to single words and close spellings so it
    cannot turn ordinary descriptive prose into an unrelated preference.
    """
    if " " in phrase:
        return None, None
    document_frequency = dict(_catalog_word_document_frequency())
    vocabulary = set(_catalog_word_vocabulary())
    vocabulary.update(key for key in REVERSE_INDEX if " " not in key)
    protected_tokens = set(STOPWORDS) | set(NEGATION_SYNTAX_TOKENS) | set(WORD_NUMBERS)
    corrected = correct_unknown_token(
        phrase,
        vocabulary,
        document_frequency,
        protected_tokens,
    )
    hit = REVERSE_INDEX.get(corrected) if corrected else None
    return (hit, corrected) if hit else (None, None)


def fuzzy_synonym_hit(phrase):
    return fuzzy_synonym_match(phrase)[0]


def _extract_preferences_uncached(raw_input, allow_fuzzy_correction=True):
    normalized_text = normalize_text(raw_input)
    tokens = tokenize(normalized_text)
    negation_scopes = find_negation_scopes(tokens)
    soft_negation_scopes = find_soft_negation_scopes(tokens)

    moods, genres, themes = set(), set(), set()
    excluded_moods, excluded_genres, excluded_themes = set(), set(), set()
    excluded_keywords = set()
    content_descriptors, excluded_content_descriptors = set(), set()
    soft_exclusions = set()
    fuzzy_corrections = {}
    ambiguous_interpretations = []

    # Track which token indices got claimed by a category match, so we
    # can build a "leftover words" bag for the TF-IDF/synopsis-similarity
    # fallback (this is what lets free descriptive text like "heist" or
    # "small town" still influence scoring even without an exact dict hit).
    claimed_indices = set()

    ngrams = build_ngrams(tokens, 4)
    ambiguous_terms = _catalog_ambiguous_terms()
    confirmed_categories = _confirmed_query_categories(
        tokens, ngrams, ambiguous_terms,
    )
    # build_ngrams already yields longest-phrase-first per position, so
    # the first successful lookup at a given index wins (greedy longest
    # match).
    for ngram in ngrams:
        start, end = ngram["start_index"], ngram["end_index"]
        if any(k in claimed_indices for k in range(start, end + 1)):
            continue  # already claimed by a longer phrase

        hit = REVERSE_INDEX.get(ngram["phrase"])
        exact_hit = hit
        if not hit:
            hit, corrected = fuzzy_synonym_match(ngram["phrase"]) if allow_fuzzy_correction else (None, None)
            if hit and corrected:
                fuzzy_corrections[ngram["phrase"]] = corrected
        if not hit:
            continue

        hit_phrase = corrected if not exact_hit and corrected else ngram["phrase"]
        ambiguous = (
            ngram["phrase"] in ambiguous_terms or hit_phrase in ambiguous_terms
        )
        field_hint = _field_category_hint(tokens, start, end)
        if not field_hint:
            category_hints = [
                category for category in confirmed_categories
                if _field_specific_hit(hit_phrase, category)
            ]
            if len(category_hints) == 1:
                field_hint = category_hints[0]
        if ambiguous and field_hint:
            field_hit = _field_specific_hit(hit_phrase, field_hint)
            if field_hit:
                hit = field_hit
                ambiguous = False
        if ambiguous and not _has_second_signal(
            ngram, tokens, ngrams, ambiguous_terms,
        ):
            negated = is_within_negation_scope(start, end, negation_scopes)
            if not negated:
                negated = is_retroactively_negated(end, tokens)
            softly_negated = is_within_negation_scope(
                start, end, soft_negation_scopes,
            )
            interpretation = {
                "surface": ngram["phrase"],
                "category": hit["category"],
                "tag": hit["tag"],
                "negated": bool(negated or softly_negated),
            }
            existing_interpretation = next(
                (
                    item for item in ambiguous_interpretations
                    if all(
                        item[key] == interpretation[key]
                        for key in ("surface", "category", "tag")
                    )
                ),
                None,
            )
            if existing_interpretation is None:
                ambiguous_interpretations.append(interpretation)
            elif interpretation["negated"]:
                existing_interpretation["negated"] = True
            if negated or softly_negated:
                soft_exclusions.add(f"{hit['category']}:{hit['tag']}")
            for k in range(start, end + 1):
                claimed_indices.add(k)
            continue

        negated = is_within_negation_scope(start, end, negation_scopes)
        if not negated:
            negated = is_retroactively_negated(end, tokens)
        softly_negated = is_within_negation_scope(start, end, soft_negation_scopes)
        if softly_negated:
            soft_exclusions.add(f"{hit['category']}:{hit['tag']}")
            for k in range(start, end + 1):
                claimed_indices.add(k)
            continue
        target_set = {
            "mood": excluded_moods if negated else moods,
            "genre": excluded_genres if negated else genres,
            "theme": excluded_themes if negated else themes,
            "content_descriptor": excluded_content_descriptors if negated else content_descriptors,
        }[hit["category"]]

        target_set.add(hit["tag"])
        if negated and exact_hit is None:
            excluded_keywords.add(ngram["phrase"])
        for k in range(start, end + 1):
            claimed_indices.add(k)

    runtime = extract_runtime_constraint(raw_input)
    if not any(runtime[key] is not None for key in ("min", "max", "target")):
        from data.movies import MOVIES

        runtime = extract_vague_runtime_constraint(
            normalized_text,
            [movie.get("runtime", 0) for movie in MOVIES],
        ) or runtime
    release_year = extract_release_year_constraint(normalized_text)
    numeric_negation_cues = {"not", "no", "without", "except", "excluding", "avoid", "avoiding", "skip", "but"}
    for index, token in enumerate(tokens):
        if (
            token.isdigit()
            and index not in claimed_indices
            and is_within_negation_scope(index, index, negation_scopes)
            and index > 0
            and tokens[index - 1] in numeric_negation_cues
            and not (index + 1 < len(tokens) and tokens[index + 1] in {"minute", "minutes", "min", "mins", "hour", "hours", "hr", "hrs"})
        ):
            excluded_keywords.add(token)
            claimed_indices.add(index)
            if (
                release_year
                and release_year["min"] == int(token)
                and release_year["max"] == int(token)
            ):
                release_year = None
            continue
        if (
            (token not in STOPWORDS or index > 0 and tokens[index - 1] in NEGATION_CUE_WORDS)
            and token not in NEGATION_SYNTAX_TOKENS
            and index not in claimed_indices
            and is_within_negation_scope(index, index, negation_scopes)
            and not token.isdigit()
        ):
            excluded_keywords.add(token)
            claimed_indices.add(index)

    # Leftover words feed the TF-IDF/cosine-similarity fallback so
    # descriptive free text not covered by the synonym dictionary still
    # contributes to matching (e.g. "hotel", "detective", "wedding").
    leftover_tokens = [t for i, t in enumerate(tokens) if i not in claimed_indices and t != ","]
    free_text_keywords = [
        token for token in remove_stopwords(leftover_tokens)
        if token not in excluded_keywords
    ]
    has_standalone_no_thanks = any(
        tokens[index:index + 2] == ["no", "thanks"]
        for index in range(len(tokens) - 1)
    )
    unclear_negation = bool(
        any(token in NEGATION_CUES for token in tokens)
        and not negation_scopes
        and not soft_negation_scopes
        and not has_standalone_no_thanks
    )

    return {
        "raw_input": raw_input,
        "normalized_text": normalized_text,
        "tokens": tokens,
        "moods": sorted(moods),
        "genres": sorted(genres),
        "themes": sorted(themes),
        "excluded_moods": sorted(excluded_moods),
        "excluded_genres": sorted(excluded_genres),
        "excluded_themes": sorted(excluded_themes),
        "excluded_keywords": sorted(excluded_keywords),
        "content_descriptors": sorted(content_descriptors),
        "excluded_content_descriptors": sorted(excluded_content_descriptors),
        "soft_exclusions": sorted(soft_exclusions),
        "fuzzy_corrections": fuzzy_corrections,
        "ambiguous_interpretations": ambiguous_interpretations,
        "unclear_negation": unclear_negation,
        "runtime": runtime,
        "release_year": release_year,
        "free_text_keywords": free_text_keywords,
        "negation_scopes": negation_scopes,
    }


_EXTRACT_PREFERENCES_CACHE = {}


def extract_preferences(raw_input, allow_fuzzy_correction=True):
    cache_key = (str(raw_input), bool(allow_fuzzy_correction))
    cached = _EXTRACT_PREFERENCES_CACHE.get(cache_key)
    if cached is None:
        cached = _extract_preferences_uncached(
            raw_input,
            allow_fuzzy_correction=allow_fuzzy_correction,
        )
        if len(_EXTRACT_PREFERENCES_CACHE) >= 4096:
            oldest_key = next(iter(_EXTRACT_PREFERENCES_CACHE))
            del _EXTRACT_PREFERENCES_CACHE[oldest_key]
        _EXTRACT_PREFERENCES_CACHE[cache_key] = copy.deepcopy(cached)
    return copy.deepcopy(cached)
