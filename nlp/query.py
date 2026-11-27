"""Structured natural-language query interpretation for the movie finder.

This module is deliberately rule-based and explainable. It separates explicit
filters from the residual semantic description before scoring the catalog.
"""

import re
import copy
from datetime import date
from functools import lru_cache

import pycountry

from .extract import (
    NEGATION_SYNTAX_TOKENS,
    REVERSE_INDEX,
    _catalog_word_vocabulary,
    extract_preferences,
    strip_non_informative_phrases,
)
from .fuzzy import correct_unknown_token
from .negation import find_negation_scopes, is_within_negation_scope
from .normalize import normalize_text
from .tokenize import STOPWORDS, tokenize
from .catalog_matching import load_query_rules
from .runtime import WORD_NUMBERS

RANKING_PATTERNS = {
    "best": ("best", "top", "good", "great", "highest rated", "highly rated", "greatest"),
    "worst": ("worst", "lowest rated", "lowest rating"),
    "popular": ("most popular", "popular", "trending"),
    "recommended": ("recommend", "recommended", "suggest"),
}

EXPLICIT_SYNTAX_TOKENS = {
    "after", "before", "since", "from", "to", "through", "until", "under",
    "over", "around", "about", "hour", "hours", "minute", "minutes", "min",
    "mins", "recent", "old", "new", "year", "years",
}

COUNTRY_ALIASES = load_query_rules()["country_demonyms"]

SHORT_COUNTRY_ALIASES = {"ph", "us", "uk", "jp", "jpn", "sk", "kr", "uae"}


@lru_cache(maxsize=1)
def _country_alias_document_frequency():
    from data.movies import MOVIES

    counts = {}
    country_terms = dict(COUNTRY_PHRASES)
    country_terms.update(COUNTRY_ALIASES)
    for alias, code in country_terms.items():
        if " " in alias:
            continue
        count = 0
        for movie in MOVIES:
            values = movie.get("production_countries") or movie.get("origin_regions") or []
            if isinstance(values, str):
                values = [values]
            country_codes = {
                str(
                    value.get("iso_3166_1", value.get("code", ""))
                    if isinstance(value, dict) else value
                ).strip().upper()
                for value in values
            }
            if code.upper() in country_codes:
                count += 1
        counts[alias] = count
    return tuple(sorted(counts.items()))


def _phrase_is_negated(text, phrase):
    tokens = tokenize(text)
    phrase_tokens = tokenize(phrase)
    if not phrase_tokens:
        return False
    scopes = find_negation_scopes(tokens)
    return any(
        tokens[start:start + len(phrase_tokens)] == phrase_tokens
        and is_within_negation_scope(start, start + len(phrase_tokens) - 1, scopes)
        for start in range(len(tokens) - len(phrase_tokens) + 1)
    )


def _phrase_is_descriptive(text, phrase):
    tokens = tokenize(text)
    phrase_tokens = tokenize(phrase)
    if not phrase_tokens:
        return False
    for start in range(len(tokens) - len(phrase_tokens) + 1):
        if tokens[start:start + len(phrase_tokens)] != phrase_tokens:
            continue
        prior = tokens[:start]
        about_index = max(
            (index for index, token in enumerate(prior) if token in {"about", "regarding"}),
            default=-1,
        )
        location_index = max(
            (index for index, token in enumerate(prior) if token in {"from", "in", "made", "produced", "country"}),
            default=-1,
        )
        if about_index > location_index:
            return True
    return False


def country_labels():
    labels = {country.alpha_2: country.name for country in pycountry.countries}
    labels.update({"CV": "Cabo Verde", "XK": "Kosovo", "SU": "Soviet Union"})
    return labels


COUNTRY_LABELS = country_labels()
COUNTRY_PHRASES = {
    " ".join(normalize_text(name).replace(",", " ").split()): code
    for code, name in COUNTRY_LABELS.items()
}


def _remove_phrases(text, phrases):
    for phrase in sorted({phrase for phrase in phrases if phrase}, key=len, reverse=True):
        text = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", text)
    return " ".join(text.split())


def detect_country(normalized_text):
    """Return a canonical ISO country code and matched phrase, when present."""
    normalized_text = " ".join(
        re.sub(r"\s*,\s*", " ", normalized_text).split()
    )
    for phrase, code in sorted(COUNTRY_PHRASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b(?:movies?|films?)\s+in\s+{re.escape(phrase)}\b", normalized_text):
            continue
        if (
            re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized_text)
            and not _phrase_is_negated(normalized_text, phrase)
            and not _phrase_is_descriptive(normalized_text, phrase)
        ):
            return code, phrase
    for phrase, code in sorted(COUNTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if phrase in SHORT_COUNTRY_ALIASES and not re.search(
            r"\b(?:movie|movies|film|films|cinema|from|made|produced|country)\b",
            normalized_text,
        ):
            continue
        if re.search(rf"\b(?:movies?|films?)\s+in\s+{re.escape(phrase)}\b", normalized_text):
            continue
        if (
            re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized_text)
            and not _phrase_is_negated(normalized_text, phrase)
            and not _phrase_is_descriptive(normalized_text, phrase)
        ):
            return code, phrase
    if not re.search(
        r"\b(?:movie|movies|film|films|cinema|from|made|country)\b",
        normalized_text,
    ):
        return "", ""
    country_vocabulary = {
        alias for alias in (*COUNTRY_PHRASES, *COUNTRY_ALIASES)
        if " " not in alias
    }
    known_vocabulary = set(_catalog_word_vocabulary()) | {
        token for phrase in REVERSE_INDEX
        for token in tokenize(phrase)
        if token != ","
    }
    protected_tokens = (
        set(STOPWORDS) | set(NEGATION_SYNTAX_TOKENS)
        | set(EXPLICIT_SYNTAX_TOKENS) | set(WORD_NUMBERS)
    )
    frequencies = dict(_country_alias_document_frequency())
    for token in normalized_text.split():
        corrected = correct_unknown_token(
            token,
            country_vocabulary,
            document_frequency=frequencies,
            protected_tokens=protected_tokens,
            known_tokens=known_vocabulary,
        )
        if (
            corrected
            and not re.search(
                rf"\b(?:movies?|films?)\s+in\s+{re.escape(corrected)}\b",
                normalized_text,
            )
            and not _phrase_is_negated(normalized_text, token)
            and not _phrase_is_descriptive(normalized_text, token)
        ):
            return COUNTRY_ALIASES.get(corrected, COUNTRY_PHRASES.get(corrected)), token
    return "", ""


def detect_ranking_intent(normalized_text):
    for intent, phrases in RANKING_PATTERNS.items():
        for phrase in phrases:
            if phrase == "good" and re.search(r"\bfeel[- ]good\b", normalized_text):
                continue
            if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized_text):
                return intent, phrase
    return "", ""


def detect_people_and_language(normalized_text):
    """Extract requested people/language text without inventing unavailable metadata."""
    people = []
    patterns = (
        r"\b(?:starring|with|directed by|written by)\s+([a-z][a-z .'-]+)",
        r"\b(?:actor|actress|director|writer)\s+([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+)+)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized_text)
        if match:
            people.append(match.group(1).strip())
    languages = []
    match = re.search(
        r"\b([a-z]+)\s+(?:language(?:\s+(?:movies?|films?))?|films?)\b",
        normalized_text,
    )
    if match and _is_language_name(match.group(1)):
        languages.append(match.group(1))
    match = re.search(r"\b(?:in|sa)\s+([a-z]+)\s+(?:movies?|films?|cinema)\b", normalized_text)
    if match and _is_language_name(match.group(1)):
        languages.append(match.group(1))
    return people, languages


def _is_language_name(value):
    try:
        pycountry.languages.lookup(value)
    except LookupError:
        return False
    return True


def detect_cast_gender_constraint(normalized_text):
    """Map explicit cast-gender requests to a structured, verifiable constraint."""
    patterns = [
        (r"\b(?:no|not|without|except|excluding|avoid|avoiding|skip|never)\b.*\b(?:woman|women|female|girls?|ladies?|females?)\b", {"type": "cast_gender", "exclude": "female"}),
        (r"\b(?:all[- ]male|male[- ]only|only men|no men|all male cast|all[- ]male cast)\b", {"type": "cast_gender", "require": "all_male"}),
        (r"\b(?:all[- ]female|female[- ]only|only women|all women|women only|all[- ]female cast)\b", {"type": "cast_gender", "require": "all_female"}),
        (r"\b(?:female lead|woman lead|women lead|with a female lead|starring a woman|starring women)\b", {"type": "cast_gender", "require": "female_lead"}),
        (r"\b(?:majority women|more women than men|mostly women|majority of the cast is women)\b", {"type": "cast_gender", "require": "majority_women"}),
        (r"\b(?:at least one woman|one woman|a woman|women present|has women)\b", {"type": "cast_gender", "require": "at_least_one_woman"}),
        (r"\b(?:no men|without men|no male|male characters only)\b", {"type": "cast_gender", "require": "no_men"}),
    ]
    for pattern, constraint in patterns:
        if re.search(pattern, normalized_text, flags=re.IGNORECASE):
            return constraint
    return None


def _build_structured_query_uncached(raw_query, content_text=None, allow_fuzzy_correction=True):
    """Return the explicit filters, ranking intent, and semantic residual text."""
    normalized = strip_non_informative_phrases(raw_query).replace(",", " ")
    country, country_phrase = detect_country(normalized)
    ranking_intent, ranking_phrase = detect_ranking_intent(normalized)
    cleaned = strip_non_informative_phrases(
        _remove_phrases(normalized, [country_phrase, ranking_phrase])
    )
    if content_text is not None:
        cleaned = strip_non_informative_phrases(content_text)
    preferences = extract_preferences(
        cleaned,
        allow_fuzzy_correction=allow_fuzzy_correction,
    )
    cast_constraint = detect_cast_gender_constraint(normalized)
    rules = load_query_rules()
    audience_intents = []
    audience_certification_limits = {}
    for label, mapping in rules["audience_occasions"].items():
        if any(re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized) for phrase in mapping["phrases"]):
            audience_intents.append(label)
            preferences["genres"] = sorted(set(preferences["genres"]) | set(mapping["genres"]))
            audience_certification_limits[label] = mapping.get("maximum_certification")
    preferences["audience_intents"] = audience_intents
    preferences["audience_certification_limits"] = audience_certification_limits
    if cast_constraint:
        preferences["cast_gender"] = cast_constraint
        preferences["free_text_keywords"] = []
    preferences["free_text_keywords"] = [
        word for word in preferences["free_text_keywords"]
        if word not in EXPLICIT_SYNTAX_TOKENS
        and word not in {"shorter"}
        and not re.fullmatch(r"\d{2,4}s?", word)
        and not re.fullmatch(r"(?:19|20)\d{2}s", word)
    ]
    if preferences["runtime"]["min"] is not None or preferences["runtime"]["max"] is not None or preferences["runtime"]["target"] is not None:
        preferences["free_text_keywords"] = [
            word for word in preferences["free_text_keywords"]
            if not re.fullmatch(r"\d+(?:\.\d+)?", word) and word not in {"long", "short", "shorter", "less", "least", "more", "longer"}
        ]

    # A rom-com is explicitly both romance and comedy, not merely romance.
    if re.search(r"\b(?:romcom|rom-com|rom com)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"romance", "comedy"})
    if re.search(r"\b(?:funny|hilarious|comedic|comedy|nakakatawa(?:ng)?)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"comedy"})
    if re.search(r"\b(?:romantic|love story)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"romance"})
    if re.search(r"\b(?:make me cry|cry|tearful)\b", normalized):
        preferences["moods"] = sorted(set(preferences["moods"]) | {"sad"})
    # "scary movies" is an explicit horror request; bare "scary" stays a mood.
    if re.search(r"\bscary\s+(?:movie|movies|film|films)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"horror"})
    if re.search(r"\b(?:haunted house|ghost story|haunting house)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"horror"})

    has_secondary_constraint = (
        any(preferences["runtime"].get(key) is not None for key in ("min", "max", "target"))
        or bool(preferences.get("release_year"))
    )
    if has_secondary_constraint:
        resolved_genres = {
            item["tag"]
            for item in preferences.get("ambiguous_interpretations", [])
            if item["category"] == "genre" and not item.get("negated")
        }
        preferences["genres"] = sorted(set(preferences["genres"]) | resolved_genres)
        preferences["ambiguous_interpretations"] = [
            item for item in preferences.get("ambiguous_interpretations", [])
            if not (item["category"] == "genre" and item["tag"] in resolved_genres)
        ]
    unresolved_ambiguous_tags = {
        (item["category"], item["tag"])
        for item in preferences.get("ambiguous_interpretations", [])
    }
    for key, category in (
        ("genres", "genre"), ("moods", "mood"), ("themes", "theme"),
    ):
        preferences[key] = [
            value for value in preferences[key]
            if (category, value) not in unresolved_ambiguous_tags
            or has_secondary_constraint and category == "genre"
        ]

    people, languages = detect_people_and_language(normalized)
    directors = re.findall(r"\bdirected by\s+([a-z][a-z .'-]+)", normalized)
    writers = re.findall(r"\bwritten by\s+([a-z][a-z .'-]+)", normalized)
    people = list(dict.fromkeys([*people, *directors, *writers]))
    filtered_people = []
    for person in people:
        person_preferences = extract_preferences(person)
        if not any((
            person_preferences["genres"],
            person_preferences["moods"],
            person_preferences["themes"],
        )) and not cast_constraint:
            filtered_people.append(person)
    people = filtered_people
    entity_requests = []
    entity_patterns = (
        ("cast", r"\b(?:starring|with)\s+([^,.;!?]+?)(?=\s+(?:in|from|under|over|before|after|without|but|movie|movies|film|films)\b|$)"),
        ("director", r"\bdirected by\s+([^,.;!?]+?)(?=\s+(?:in|from|under|over|before|after|without|but|movie|movies|film|films)\b|$)"),
        ("writer", r"\bwritten by\s+([^,.;!?]+?)(?=\s+(?:in|from|under|over|before|after|without|but|movie|movies|film|films)\b|$)"),
    )
    for field, pattern in entity_patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        value = match.group(1).strip()
        parsed_value = extract_preferences(value)
        if field == "cast" and (cast_constraint or any((
            parsed_value["genres"], parsed_value["moods"], parsed_value["themes"],
        ))):
            continue
        if value:
            entity_requests.append({"field": field, "value": value})
    certification_match = re.search(
        r"\b(?:rated|rating|certification|certificate)\s+"
        r"(pg-?13|nc-?17|g|pg|r)\b",
        normalized,
    )
    certifications = [certification_match.group(1)] if certification_match else []
    entity_requests.extend(
        {"field": "language", "value": language}
        for language in languages
    )
    entity_requests.extend(
        {"field": "certification", "value": certification}
        for certification in certifications
    )
    preferences["entity_requests"] = entity_requests
    return {
        "raw_query": raw_query,
        "normalized_query": normalized,
        "country": country,
        "country_phrase": country_phrase,
        "ranking_intent": ranking_intent,
        "ranking_phrase": ranking_phrase,
        "cleaned_text": cleaned,
        "preferences": preferences,
        "semantic_description": preferences["free_text_keywords"],
        "people": people,
        "languages": languages,
        "unsupported_filters": {
            "people": people,
            "directors": directors,
            "writers": writers,
            "languages": languages,
            "certifications": certifications,
        },
        "debug": {
            "country": country,
            "ranking_intent": ranking_intent,
            "genres": preferences["genres"],
            "excluded_genres": preferences["excluded_genres"],
            "moods": preferences["moods"],
            "themes": preferences["themes"],
            "release_year": preferences["release_year"],
            "semantic_description": preferences["free_text_keywords"],
            "people": people,
            "languages": languages,
            "cast_gender": preferences.get("cast_gender"),
            "audience_intents": audience_intents,
        },
    }


_BUILD_STRUCTURED_QUERY_CACHE = {}


def build_structured_query(raw_query, content_text=None, allow_fuzzy_correction=True):
    cache_key = (
        str(raw_query),
        None if content_text is None else str(content_text),
        bool(allow_fuzzy_correction),
    )
    cached = _BUILD_STRUCTURED_QUERY_CACHE.get(cache_key)
    if cached is None:
        cached = _build_structured_query_uncached(
            raw_query,
            content_text=content_text,
            allow_fuzzy_correction=allow_fuzzy_correction,
        )
        if len(_BUILD_STRUCTURED_QUERY_CACHE) >= 4096:
            oldest_key = next(iter(_BUILD_STRUCTURED_QUERY_CACHE))
            del _BUILD_STRUCTURED_QUERY_CACHE[oldest_key]
        _BUILD_STRUCTURED_QUERY_CACHE[cache_key] = copy.deepcopy(cached)
    return copy.deepcopy(cached)
