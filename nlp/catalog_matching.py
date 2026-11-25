"""Data-derived title and series matching."""

import re
import unicodedata
from difflib import SequenceMatcher


SERIES_MEMBER_MATCH_PERCENT = 100

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20",
}


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
    text = re.sub(r"\bv(?:\\.|\\b)", " vs ", text)
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
            if shared >= 3:
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
            "base": sorted({normalize_title(member.get("title", "")) for member in members}, key=len)[0],
            "rule": rule,
            "titles": sorted((member.get("title", "") for member in members), key=str.casefold),
        })
    result.sort(key=lambda group: tuple(group["titles"]))
    return result
