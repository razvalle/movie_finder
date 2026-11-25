"""Data-derived title and series matching."""

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path


SERIES_MEMBER_MATCH_PERCENT = 100
QUERY_RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "query_rules.json"
_TITLE_INDEX_CACHE = {}


@lru_cache(maxsize=1)
def load_query_rules():
    import json

    with QUERY_RULES_PATH.open(encoding="utf-8") as source:
        return json.load(source)

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20",
}
_PREFIX_FILLERS = {"a", "an", "and", "of", "the", "to", "in", "on"}


def _roman_value(value):
    if not re.fullmatch(r"(?i)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", value):
        return None
    lookup = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    lower = value.lower()
    for index, char in enumerate(lower):
        number = lookup.get(char, 0)
        total += -number if index + 1 < len(lower) and number < lookup[lower[index + 1]] else number
    return str(total) if total else None


def normalize_title(value):
    text = str(value or "").replace("·", " ").replace("&", " and ")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.casefold()
    text = re.sub(r"\bv\.?\b", " vs ", text)
    text = re.sub(r"[‘’']", "", text)
    text = text.replace("-", " ").replace("–", " ").replace("—", " ")
    text = re.sub(r"\bvs\b", "vs", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = text.split()
    words = [_NUMBER_WORDS.get(word, word) for word in words]
    if words and re.fullmatch(r"[ivxlcdm]+", " ".join(words).replace(" ", "")):
        roman = _roman_value(" ".join(words).replace(" ", ""))
        if roman:
            words = [roman]
    else:
        words = [(_roman_value(word) or word) if re.fullmatch(r"[ivxlcdm]+", word) else word for word in words]
    compact = " ".join(words)
    compact = re.sub(r"\bhigh school\b|\bhighschool\b", "highschool", compact)
    compact = re.sub(r"\bpart\s+(\d+)\b", r"\1", compact)
    compact = re.sub(r"\bchapter\s+(\d+)\b", r"\1", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    return re.sub(r"^(?:the|a|an)\s+", "", compact)


def title_aliases(movie):
    aliases = [normalize_title(movie.get("title", ""))]
    for key in ("original_title", "alternate_title", "alternate_titles"):
        value = movie.get(key)
        if isinstance(value, str):
            aliases.append(normalize_title(value))
        elif isinstance(value, list):
            aliases.extend(normalize_title(item) for item in value if isinstance(item, str))
    for title in (movie.get("title", ""), movie.get("original_title", "")):
        if not isinstance(title, str):
            continue
        for segment in re.split(r"\s*[:—–-]\s*", title):
            if segment.strip():
                aliases.append(normalize_title(segment))
    return list(dict.fromkeys(alias for alias in aliases if alias))


def build_title_index(movies):
    """Map normalized canonical and alternate title spans to their movie records."""
    cache_key = id(movies)
    cached = _TITLE_INDEX_CACHE.get(cache_key)
    if cached and cached[0] is movies:
        return cached[1]
    index = {}
    for movie in movies:
        for alias in title_aliases(movie):
            index.setdefault(alias, []).append(movie)
    if len(_TITLE_INDEX_CACHE) >= 4:
        _TITLE_INDEX_CACHE.clear()
    _TITLE_INDEX_CACHE[cache_key] = (movies, index)
    return index


def detect_title_spans(query, movies, short_title_words=(), risky_title_vocabulary=None):
    """Return all data-derived title spans, protecting short/stopword/digit titles."""
    raw = str(query or "")
    normalized_query = normalize_title(raw)
    words = normalized_query.split()
    fillers = {"movie", "movies", "film", "films"}
    without_fillers = [word for word in words if word not in fillers]
    year_removed = [
        word for word in without_fillers
        if not re.fullmatch(r"(?:18|19|20)\d{2}", word)
    ]
    whole_keys = {
        " ".join(words),
        " ".join(without_fillers),
        " ".join(year_removed),
    }
    quoted = {
        normalize_title(match.group(1))
        for match in re.finditer(r"""['"]([^'"]{1,100})['"]""", raw)
    }
    normalized_aliases = build_title_index(movies)
    spans = []
    for alias, records in normalized_aliases.items():
        alias_words = alias.split()
        if not alias_words:
            continue
        is_short_risky = (
            len(alias.replace(" ", "")) <= 3
            or alias in short_title_words
            or all(word in short_title_words for word in alias_words)
            or alias.isdigit()
            or all(word.isdigit() for word in alias_words)
        )
        for start in range(len(words) - len(alias_words) + 1):
            end = start + len(alias_words)
            if words[start:end] != alias_words:
                continue
            surrounding_words = words[:start] + words[end:]
            is_whole = alias in whole_keys or all(
                word in fillers
                or re.fullmatch(r"(?:18|19|20)\d{2}", word)
                for word in surrounding_words
            )
            cue_index = start - 1
            if cue_index >= 0 and words[cue_index] in {"a", "an", "the"}:
                cue_index -= 1
            explicitly_named = cue_index >= 0 and words[cue_index] in {
                "called", "named", "titled",
            }
            explicitly_quoted = alias in quoted
            if is_short_risky and not (is_whole or explicitly_named or explicitly_quoted):
                continue
            spans.append({
                "start": start,
                "end": end,
                "alias": alias,
                "movies": records,
                "whole_query": is_whole,
                "explicit": explicitly_named or explicitly_quoted,
                "risk_terms": (risky_title_vocabulary or {}).get(alias, []),
            })
    spans.sort(key=lambda span: (
        not span["whole_query"],
        -(span["end"] - span["start"]),
        span["start"],
        span["alias"],
    ))
    return spans


def title_risk_vocabulary(movies):
    """Return title vocabulary collisions with catalog concepts and parser syntax."""
    from data.synonyms import (
        GENRE_SYNONYMS,
        MOOD_SYNONYMS,
        THEME_SYNONYMS,
        CONTENT_DESCRIPTOR_SYNONYMS,
    )

    vocabulary = set()
    for table in (GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS, CONTENT_DESCRIPTOR_SYNONYMS):
        for canonical, forms in table.items():
            vocabulary.update(normalize_title(form) for form in (canonical, *forms))
    vocabulary.update({
        "short", "quick", "long", "hour", "hours", "minute", "minutes",
        "year", "years", "half", "hundred", "thirty", "forty", "fifty",
        "sixty", "seventy", "eighty", "ninety", *_NUMBER_WORDS.keys(),
    })
    vocabulary_words = {word for phrase in vocabulary for word in phrase.split()}
    risks = {}
    for movie in movies:
        for title in title_aliases(movie):
            collisions = {
                word for word in title.split()
                if word in vocabulary_words or word.isdigit()
            }
            if collisions:
                risks[title] = sorted(collisions)
    return risks


def resolve_like_title(query, movies):
    """Resolve a 'like X' source and return unresolved text for optional suggestions."""
    source = find_like_source_text(query)
    if not source:
        return None, []
    spans = detect_title_spans(source, movies)
    if spans and spans[0]["whole_query"]:
        span = spans[0]
        matches = span["movies"]
        canonical_matches = [
            movie for movie in matches
            if normalize_title(movie.get("title", "")) == span["alias"]
        ]
        if canonical_matches:
            matches = canonical_matches
        return matches[0], []
    without_trailing_filler = re.sub(
        r"\b(?:movie|movies|film|films)\s*$", "", source,
    ).strip()
    if without_trailing_filler and without_trailing_filler != source:
        spans = detect_title_spans(without_trailing_filler, movies)
        if spans and spans[0]["whole_query"]:
            return spans[0]["movies"][0], []
    aliases = build_title_index(movies)
    ranked_suggestions = sorted(
        (
            (SequenceMatcher(None, source, alias).ratio(), alias, records[0])
            for alias, records in aliases.items()
        ),
        key=lambda item: (-item[0], item[1]),
    )
    suggestions = []
    seen_ids = set()
    for score, _, movie in ranked_suggestions:
        movie_id = str(movie.get("id"))
        if score < 0.55 or movie_id in seen_ids:
            continue
        seen_ids.add(movie_id)
        suggestions.append(movie)
        if len(suggestions) == 5:
            break
    return None, suggestions


def find_like_source_text(query):
    normalized = normalize_title(query)
    match = re.search(
        r"\b(?:similar to|in the style of|such as|something like|movies like|films like|like)\s+(.+)$",
        normalized,
    )
    return match.group(1).strip() if match else None


def _base_marker(title):
    words = title.split()
    marker_match = re.search(r"\s+(?:(\d+|[ivxlcdm]+|[a-z]+))$", title)
    if marker_match:
        marker = marker_match.group(1)
        if marker in _NUMBER_WORDS or marker.isdigit() or re.fullmatch(r"[ivxlcdm]+", marker):
            return " ".join(words[:-1])
    if " and the " in f" {title} ":
        return title.split(" and the ", 1)[0].strip()
    for marker in ("part", "chapter"):
        if re.search(rf"\b{marker}\s+\S+", title):
            base, _ = re.split(rf"\b{marker}\b", title, maxsplit=1)
            return base.strip()
    return ""


def derive_series_groups(movies):
    """Generic union-based series derivation for review and runtime use."""
    canonicals = {}
    for movie in movies:
        canonicals.setdefault(normalize_title(movie.get("title", "")), []).append(movie)

    parents = list(range(len(movies)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    movie_index = {str(movie.get("id")): index for index, movie in enumerate(movies)}
    rules = {}
    titles = [normalize_title(movie.get("title", "")) for movie in movies]
    bases = {}
    for index, title in enumerate(titles):
        base = _base_marker(title)
        if base:
            bases.setdefault(base, []).append(index)

    for base, indexes in bases.items():
        base_indexes = [index for index, title in enumerate(titles) if title == base]
        all_indexes = list(dict.fromkeys([*base_indexes, *indexes]))
        if len(all_indexes) >= 2:
            for index in all_indexes[1:]:
                union(all_indexes[0], index)
            rules.setdefault(base, "sequel-marker")
    for base, indexes in bases.items():
        if len(indexes) >= 2:
            for index in indexes[1:]:
                union(indexes[0], index)
            rules.setdefault(base, "sequel-marker")

    for left, title_left in enumerate(titles):
        for right in range(left + 1, len(titles)):
            title_right = titles[right]
            left_words, right_words = title_left.split(), title_right.split()
            shared = 0
            for left_word, right_word in zip(left_words, right_words):
                if left_word != right_word:
                    break
                shared += 1
            if shared >= 2 and sum(word not in _PREFIX_FILLERS for word in left_words[:shared]) >= 2:
                union(left, right)
                base = " ".join(left_words[:shared])
                rules.setdefault(base, "two-word-prefix")

    groups = {}
    for index, movie in enumerate(movies):
        root = find(index)
        groups.setdefault(root, []).append(movie)
    result = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_titles = {normalize_title(member.get("title", "")) for member in members}
        if len(member_titles) < 2:
            continue
        common = sorted(member_titles)
        rule = "two-word-prefix"
        for title in common:
            base = _base_marker(title)
            if base and any(normalize_title(member.get("title", "")) == base for member in members):
                rule = "sequel-marker"
            if base:
                rule = "sequel-marker"
                break
        result.append({
            "base": sorted(
                {normalize_title(member.get("title", "")) for member in members},
                key=lambda title: (len(title), title),
            )[0],
            "rule": rule,
            "titles": sorted(
                {member.get("title", "") for member in members},
                key=lambda title: (title.casefold(), title),
            ),
        })
    result.sort(key=lambda group: tuple(group["titles"]))
    return result
