"""Structured natural-language query interpretation for the movie finder.

This module is deliberately rule-based and explainable. It separates explicit
filters from the residual semantic description before scoring the catalog.
"""

import re
from datetime import date

import pycountry

from .extract import extract_preferences
from .normalize import normalize_text

RANKING_PATTERNS = {
    "best": ("best", "top", "highest rated", "highly rated", "greatest"),
    "popular": ("most popular", "popular", "trending"),
    "recommended": ("recommend", "recommended", "suggest"),
}

EXPLICIT_SYNTAX_TOKENS = {
    "after", "before", "since", "from", "to", "through", "until", "under",
    "over", "around", "about", "hour", "hours", "minute", "minutes", "min",
    "mins", "recent", "old", "new", "year", "years",
}

COUNTRY_ALIASES = {
    "ph": "PH", "philippines": "PH", "philippine": "PH", "filipino": "PH",
    "pinoy": "PH", "pinas": "PH", "usa": "US", "us movies": "US",
    "american": "US", "uk": "GB", "british": "GB", "english": "GB",
    "indian": "IN", "japanese": "JP", "korean": "KR", "south korean": "KR",
    "chinese": "CN", "french": "FR", "german": "DE", "italian": "IT",
    "spanish": "ES", "mexican": "MX", "canadian": "CA", "australian": "AU",
    "brazilian": "BR", "argentinian": "AR", "russian": "RU", "ukrainian": "UA",
    "polish": "PL", "turkish": "TR", "dutch": "NL", "belgian": "BE",
    "swedish": "SE", "danish": "DK", "norwegian": "NO", "finnish": "FI",
    "irish": "IE", "scottish": "GB", "welsh": "GB", "new zealand": "NZ",
    "south african": "ZA", "nigerian": "NG", "iranian": "IR", "israeli": "IL",
    "thai": "TH", "vietnamese": "VN", "filipino": "PH", "indonesian": "ID",
    "malaysian": "MY", "singaporean": "SG", "colombian": "CO", "chilean": "CL",
    "peruvian": "PE", "czech": "CZ", "hungarian": "HU", "romanian": "RO",
    "greek": "GR", "portuguese": "PT", "austrian": "AT", "swiss": "CH",
    "icelandic": "IS", "uae": "AE",
}


def country_labels():
    labels = {country.alpha_2: country.name for country in pycountry.countries}
    labels.update({"CV": "Cabo Verde", "XK": "Kosovo", "SU": "Soviet Union"})
    return labels


COUNTRY_LABELS = country_labels()
COUNTRY_PHRASES = {
    normalize_text(name).replace(",", " ").strip(): code
    for code, name in COUNTRY_LABELS.items()
}


def _remove_phrases(text, phrases):
    for phrase in sorted({phrase for phrase in phrases if phrase}, key=len, reverse=True):
        text = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", text)
    return " ".join(text.split())


def detect_country(normalized_text):
    """Return a canonical ISO country code and matched phrase, when present."""
    for phrase, code in sorted(COUNTRY_PHRASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized_text):
            return code, phrase
    for phrase, code in sorted(COUNTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized_text):
            return code, phrase
    return "", ""


def detect_ranking_intent(normalized_text):
    for intent, phrases in RANKING_PATTERNS.items():
        for phrase in phrases:
            if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized_text):
                return intent, phrase
    return "", ""


def detect_people_and_language(normalized_text):
    """Extract requested people/language text without inventing unavailable metadata."""
    people = []
    for pattern in (r"(?:starring|actor|actress|directed by|director|written by|writer)\s+([a-z][a-z .'-]+)",):
        match = re.search(pattern, normalized_text)
        if match:
            people.append(match.group(1).strip())
    languages = []
    match = re.search(r"\b([a-z]+)\s+(?:language|language movies|films)\b", normalized_text)
    if match:
        languages.append(match.group(1))
    return people, languages


def build_structured_query(raw_query, content_text=None):
    """Return the explicit filters, ranking intent, and semantic residual text."""
    normalized = normalize_text(raw_query).replace(",", " ")
    country, country_phrase = detect_country(normalized)
    ranking_intent, ranking_phrase = detect_ranking_intent(normalized)
    cleaned = _remove_phrases(normalized, [country_phrase, ranking_phrase])
    if content_text is not None:
        cleaned = content_text
    preferences = extract_preferences(cleaned)
    preferences["free_text_keywords"] = [
        word for word in preferences["free_text_keywords"]
        if word not in EXPLICIT_SYNTAX_TOKENS and not re.fullmatch(r"\d{2,4}", word)
    ]

    # A rom-com is explicitly both romance and comedy, not merely romance.
    if re.search(r"\b(?:romcom|rom-com|rom com)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"romance", "comedy"})
    if re.search(r"\b(?:funny|hilarious|comedic|comedy)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"comedy"})
    if re.search(r"\b(?:romantic|love story)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"romance"})
    # "scary movies" is an explicit horror request; bare "scary" stays a mood.
    if re.search(r"\bscary\s+(?:movie|movies|film|films)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"horror"})
    if re.search(r"\b(?:haunted house|ghost story|haunting house)\b", normalized):
        preferences["genres"] = sorted(set(preferences["genres"]) | {"horror"})

    people, languages = detect_people_and_language(normalized)
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
            "languages": languages,
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
        },
    }
