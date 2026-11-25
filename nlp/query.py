"""Structured natural-language query interpretation for the movie finder.

This module is deliberately rule-based and explainable. It separates explicit
filters from the residual semantic description before scoring the catalog.
"""

import re
from datetime import date
from difflib import SequenceMatcher

import pycountry

from .extract import extract_preferences
from .negation import find_negation_scopes, is_within_negation_scope
from .normalize import normalize_text
from .tokenize import tokenize
from .catalog_matching import load_query_rules

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
    for phrase, code in sorted(COUNTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if len(phrase) < 5 or " " in phrase:
            continue
        if not re.search(r"\b(?:movie|movies|film|films|cinema|from|made|country)\b", normalized_text):
            continue
        if re.search(rf"\b(?:movies?|films?)\s+in\s+{re.escape(phrase)}\b", normalized_text):
            continue
        for token in normalized_text.split():
            if (
                SequenceMatcher(None, token, phrase).ratio() >= 0.88
                and not _phrase_is_negated(normalized_text, token)
                and not _phrase_is_descriptive(normalized_text, token)
            ):
                return code, token
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


def build_structured_query(raw_query, content_text=None):
    """Return the explicit filters, ranking intent, and semantic residual text."""
    normalized = normalize_text(raw_query).replace(",", " ")
    country, country_phrase = detect_country(normalized)
    ranking_intent, ranking_phrase = detect_ranking_intent(normalized)
    cleaned = _remove_phrases(normalized, [country_phrase, ranking_phrase])
    if content_text is not None:
        cleaned = content_text
    preferences = extract_preferences(cleaned)
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
