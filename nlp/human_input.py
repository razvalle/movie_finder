"""Deterministic, catalog-aware cleanup for noisy human search input."""

import html
import json
import re
import unicodedata
from functools import lru_cache
from copy import deepcopy
from pathlib import Path

from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)
from .fuzzy import correct_unknown_token
from .normalize import normalize_text
from .tokenize import STOPWORDS

_RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "human_input_rules.json"
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    "…": " ",
})
_RULES_CACHE = None
_VOCABULARY_CACHE = {}


@lru_cache(maxsize=1)
def _everyday_concept_entries():
    path = _RULES_PATH.parent / "everyday_concepts.json"
    with path.open(encoding="utf-8") as concepts_file:
        data = json.load(concepts_file)
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Everyday-concept data must contain an entries list.")
    return tuple(entries)


def load_human_input_rules():
    global _RULES_CACHE
    if _RULES_CACHE is None:
        with _RULES_PATH.open(encoding="utf-8") as rules_file:
            rules = json.load(rules_file)
        if not isinstance(rules.get("slang"), dict) or not isinstance(rules.get("emoji"), dict):
            raise ValueError("Human-input rules must define slang and emoji mappings.")
        _RULES_CACHE = rules
    return _RULES_CACHE


def load_everyday_concepts():
    return deepcopy(_everyday_concept_entries())


def match_everyday_concepts(query):
    normalized = normalize_text(query).casefold()
    matches = []
    seen = set()
    for entry in load_everyday_concepts():
        for phrase in entry.get("phrases", []):
            phrase_key = normalize_text(phrase).casefold()
            if not phrase_key:
                continue
            if re.search(rf"(?<!\w){re.escape(phrase_key)}(?!\w)", normalized):
                key = (entry["id"], phrase_key)
                if key not in seen:
                    matches.append({**entry, "surface": phrase})
                    seen.add(key)
                break
    return matches


def _catalog_vocabulary(movies):
    cache_key = id(movies)
    cached = _VOCABULARY_CACHE.get(cache_key)
    if cached is not None and cached[0] is movies:
        return cached[1]
    document_frequency = {}
    title_tokens = set()
    title_forms = {}
    fields = (
        "title", "original_title", "alternate_titles", "alt_titles", "synopsis",
        "overview", "tagline", "genres", "themes", "mood_tags", "keywords",
        "content_descriptors",
    )
    for movie in movies:
        movie_tokens = set()
        title_values = [
            movie.get("title", ""),
            movie.get("original_title", ""),
            *(
                movie.get("alternate_titles")
                if isinstance(movie.get("alternate_titles"), list)
                else [movie.get("alternate_titles")]
            ),
            *(
                movie.get("alt_titles")
                if isinstance(movie.get("alt_titles"), list)
                else [movie.get("alt_titles")]
            ),
        ]
        for title in title_values:
            if not isinstance(title, str):
                continue
            words = [match.group(0).casefold() for match in _WORD_PATTERN.finditer(title)]
            title_tokens.update(words)
            if words:
                title_forms["".join(words)] = " ".join(words)
        for field in fields:
            value = movie.get(field)
            values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            for item in values:
                if isinstance(item, str):
                    movie_tokens.update(
                        match.group(0).casefold()
                        for match in _WORD_PATTERN.finditer(item)
                    )
        for token in movie_tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1

    canonical_terms = set()
    for table in (
        GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS,
        CONTENT_DESCRIPTOR_SYNONYMS,
    ):
        for canonical, aliases in table.items():
            canonical_terms.update(
                match.group(0).casefold()
                for match in _WORD_PATTERN.finditer(canonical)
            )
            for alias in aliases:
                canonical_terms.update(
                    match.group(0).casefold()
                    for match in _WORD_PATTERN.finditer(alias)
                )
    spelling_vocabulary = frozenset(
        token for token in document_frequency if document_frequency[token] >= 2
    ) | frozenset(canonical_terms)
    segmentation_vocabulary = spelling_vocabulary | frozenset(STOPWORDS)
    result = (
        frozenset(title_tokens),
        title_forms,
        spelling_vocabulary,
        segmentation_vocabulary,
        document_frequency,
    )
    _VOCABULARY_CACHE.clear()
    _VOCABULARY_CACHE[cache_key] = (movies, result)
    return result


def _segment_joined_word(token, vocabulary, document_frequency, title_forms):
    lowered = token.casefold()
    if lowered in title_forms:
        return title_forms[lowered]
    if len(lowered) < 8 or lowered in vocabulary:
        return token
    best = [None] * (len(lowered) + 1)
    best[0] = (0.0, ())
    for end in range(1, len(lowered) + 1):
        for start in range(max(0, end - 18), end - 1):
            prefix = best[start]
            piece = lowered[start:end]
            if prefix is None or piece not in vocabulary:
                continue
            pieces = prefix[1] + (piece,)
            if len(pieces) > 4:
                continue
            score = prefix[0] + min(5.0, document_frequency.get(piece, 0) ** 0.5) - 2.0
            current = best[end]
            if current is None or score > current[0]:
                best[end] = (score, pieces)
    result = best[-1]
    if result and len(result[1]) >= 2:
        return " ".join(result[1])
    return token


def _repair_repeated_letters(token, vocabulary):
    candidates = set()
    lowered = token.casefold()
    if lowered in vocabulary:
        return None
    for match in re.finditer(r"(.)\1+", token, flags=re.IGNORECASE):
        run = match.group(0)
        for keep in range(1, len(run)):
            candidate = token[:match.start()] + run[:keep] + token[match.end():]
            normalized = candidate.casefold()
            if normalized in vocabulary and normalized not in STOPWORDS:
                candidates.add(candidate)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _normalize_word(
    token,
    title_tokens,
    title_forms,
    spelling_vocabulary,
    segmentation_vocabulary,
    document_frequency,
    correct_spelling,
):
    lowered = token.casefold()
    if any(char.isdigit() for char in token):
        return token, None
    if lowered not in title_tokens:
        repaired = _repair_repeated_letters(token, spelling_vocabulary)
        if repaired:
            return repaired, "elongation"
        segmented = _segment_joined_word(
            token, segmentation_vocabulary, document_frequency, title_forms,
        )
        if segmented != token:
            return segmented, "joined words"
    if correct_spelling and lowered not in segmentation_vocabulary:
        corrected = correct_unknown_token(
            lowered,
            spelling_vocabulary,
            document_frequency=document_frequency,
            protected_tokens=STOPWORDS,
            known_tokens=title_tokens,
        )
        if corrected:
            return corrected, "spelling"
    return token, None


def normalize_human_input(value, movies, *, correct_spelling=True):
    original = str(value or "")
    repairs = []
    text = html.unescape(original)
    if text != original:
        repairs.append("HTML entities decoded")
    if any(char.isspace() and char != " " for char in text):
        repairs.append("nonstandard whitespace normalized")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in text):
        repairs.append("invisible control characters removed")
    stripped = re.sub(r"<[^>]*>", " ", text)
    if stripped != text:
        repairs.append("HTML markup removed")
    text = unicodedata.normalize("NFKC", stripped)
    if text != stripped:
        repairs.append("Unicode compatibility normalized")
    punctuated = text.translate(_PUNCTUATION)
    if punctuated != text:
        repairs.append("smart punctuation normalized")
    text = re.sub(r"\.{2,}", " ", punctuated)
    chars = []
    for char in text:
        category = unicodedata.category(char)
        if (char.isspace() or category == "Zs") and char != " ":
            chars.append(" ")
        elif char == " ":
            chars.append(" ")
        elif category in {"Cc", "Cf", "Cs"}:
            continue
        else:
            chars.append(char)
    text = "".join(chars)
    rules = load_human_input_rules()

    for symbol in sorted(rules.get("emoji", {}), key=len, reverse=True):
        if symbol in text:
            text = text.replace(symbol, f" {rules['emoji'][symbol]} ")
            repairs.append("emoji interpreted")
    for emoticon in sorted(rules.get("emoticons", {}), key=len, reverse=True):
        replacement = rules["emoticons"][emoticon]
        text, count = re.subn(re.escape(emoticon), f" {replacement} ", text, flags=re.IGNORECASE)
        if count:
            repairs.append("emoticon interpreted")
    text, hashtag_count = re.subn(
        r"#([^\W_]+)",
        lambda match: _CAMEL_BOUNDARY.sub(" ", match.group(1)),
        text,
        flags=re.UNICODE,
    )
    if hashtag_count:
        repairs.append("hashtag expanded")
    for slang, replacement in sorted(
        rules.get("slang", {}).items(), key=lambda item: len(item[0]), reverse=True,
    ):
        text, count = re.subn(
            rf"(?<!\w){re.escape(slang)}(?!\w)",
            replacement,
            text,
            flags=re.IGNORECASE | re.UNICODE,
        )
        if count:
            repairs.append("slang expanded")

    vocabulary = _catalog_vocabulary(movies)
    title_tokens, title_forms, spelling_vocabulary, segmentation_vocabulary, document_frequency = vocabulary
    segmentation_vocabulary = segmentation_vocabulary | frozenset(
        term.casefold() for term in rules.get("protected_query_terms", [])
    )
    segmentation_vocabulary = segmentation_vocabulary | frozenset(
        match.group(0).casefold()
        for entry in _everyday_concept_entries()
        for phrase in entry.get("phrases", [])
        for match in _WORD_PATTERN.finditer(phrase)
    )
    correction_kinds = []

    def normalize_match(match):
        word, correction = _normalize_word(
            match.group(0), title_tokens, title_forms, spelling_vocabulary,
            segmentation_vocabulary, document_frequency, correct_spelling,
        )
        if correction:
            correction_kinds.append(correction)
        return word

    normalized = _WORD_PATTERN.sub(normalize_match, text)
    if "joined words" in correction_kinds:
        repairs.append("joined words split")
    if "elongation" in correction_kinds:
        repairs.append("elongation normalized")
    if "spelling" in correction_kinds:
        repairs.append("catalog spelling corrected")
    for char in normalized:
        if unicodedata.category(char) == "So":
            normalized = normalized.replace(char, " ")
            if "unmapped symbol removed" not in repairs:
                repairs.append("unmapped symbol removed")
    return {
        "text": " ".join(normalized.split()),
        "repairs": list(dict.fromkeys(repairs)),
    }
