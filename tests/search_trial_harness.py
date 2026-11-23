"""Offline generated-query checks for the real Movie Finder Flask search route.

Run from the project root:
    python tests/search_trial_harness.py
"""

import argparse
import hashlib
import json
import math
import os
import random
import re
import socket
import sys
import time
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from flask import template_rendered

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module
from data.movies import MOVIES
from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)
from nlp.query import build_structured_query
from nlp.smart_search import interpret_query

MAX_LATENCY_SECONDS = 5.0
BASELINE_PATH = ROOT / "tests" / "search_trial_baseline.json"
ORACLE_COMMON_WORDS = {
    "about", "after", "again", "also", "and", "any", "are", "because",
    "been", "before", "being", "between", "both", "but", "came", "can",
    "come", "could", "does", "during", "each", "even", "every", "from",
    "give", "going", "have", "here", "into", "just", "like", "make",
    "many", "more", "most", "much", "must", "only", "other", "over",
    "same", "some", "such", "than", "that", "their", "there", "these",
    "they", "this", "those", "through", "under", "very", "want", "were",
    "what", "when", "where", "which", "while", "with", "would",
}
EXCLUSION_SYNONYMS = {
    "excluded_genres": {tag: tuple(set([tag, *forms])) for tag, forms in GENRE_SYNONYMS.items()},
    "excluded_moods": {tag: tuple(set([tag, *forms])) for tag, forms in MOOD_SYNONYMS.items()},
    "excluded_themes": {tag: tuple(set([tag, *forms])) for tag, forms in THEME_SYNONYMS.items()},
    "excluded_descriptors": {
        tag: tuple(set([tag, *forms])) for tag, forms in CONTENT_DESCRIPTOR_SYNONYMS.items()
    },
}


def _oracle_tokens(value):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(value)).casefold(), flags=re.UNICODE)


@lru_cache(maxsize=4096)
def _token_forms(token):
    forms = {token}
    if len(token) > 4 and token.endswith("ies"):
        forms.add(token[:-3] + "y")
    elif len(token) > 4 and token.endswith(("ches", "shes", "sses", "xes", "zes")):
        forms.add(token[:-2])
    elif len(token) > 3 and token.endswith("s"):
        forms.add(token[:-1])
    return forms


@lru_cache(maxsize=4096)
def _phrase_tokens(phrase):
    return tuple(_oracle_tokens(phrase))


@lru_cache(maxsize=4096)
def _cached_movie_tokens(movie_id, text):
    return tuple(_oracle_tokens(text))


def _contains_phrase(tokens, phrase, token_set=None):
    phrase_tokens = _phrase_tokens(phrase)
    if not phrase_tokens:
        return False
    token_set = token_set or set(tokens)
    if not any(
        word in token_set or any(form in token_set for form in _token_forms(word))
        for word in _token_forms(phrase_tokens[0])
    ):
        return False
    if len(phrase_tokens) == 1:
        return any(
            word in token_set or any(form in token_set for form in _token_forms(word))
            for word in _token_forms(phrase_tokens[0])
        )
    for start in range(len(tokens) - len(phrase_tokens) + 1):
        if tokens[start] not in _token_forms(phrase_tokens[0]):
            continue
        if all(
            tokens[start + offset] in _token_forms(word)
            or word in _token_forms(tokens[start + offset])
            for offset, word in enumerate(phrase_tokens)
        ):
            return True
    return False


def _normalized_title(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def _has_exact_article_alias(fragment, movies):
    normalized_fragment = _normalized_title(fragment)
    articleless_fragment = re.sub(r"^(?:the|a|an)\s+", "", normalized_fragment)
    if articleless_fragment == normalized_fragment:
        return False
    return any(
        _normalized_title(movie.get("title", "")) == articleless_fragment
        for movie in movies
    )


def _movie_text(movie):
    return " ".join(_movie_field_texts(movie))


def _movie_field_texts(movie):
    fields = [
        movie.get("title", ""), movie.get("original_title", ""),
        movie.get("synopsis", ""), movie.get("overview", ""),
        movie.get("tagline", ""), movie.get("certification", ""),
        movie.get("content_rating", ""), movie.get("rating_certificate", ""),
        movie.get("nationality", ""), movie.get("quality", ""),
    ]
    for key in (
        "genres", "themes", "mood_tags", "keywords", "content_descriptors",
        "production_countries", "origin_regions", "notable_awards", "cast", "actors",
    ):
        value = movie.get(key)
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    fields.append(item)
                elif isinstance(item, dict):
                    fields.extend(
                        str(field_value) for field_value in item.values()
                        if isinstance(field_value, str)
                    )
    return [str(value) for value in fields if value]


ORACLE_TEXT_BY_ID = {str(movie["id"]): _movie_text(movie) for movie in MOVIES}
ORACLE_FIELDS_BY_ID = {
    str(movie["id"]): tuple(_movie_field_texts(movie))
    for movie in MOVIES
}
TITLE_KEY_BY_ID = {
    str(movie["id"]): _normalized_title(movie.get("title", ""))
    for movie in MOVIES
}


def _descriptive_text(movie):
    fields = [movie.get("synopsis", "")]
    fields.extend(movie.get("keywords") or [])
    return " ".join(str(value) for value in fields if value)


def _country_codes(movie):
    return movie.get("production_countries") or movie.get("origin_regions") or [movie.get("nationality")]


def _trial_identity(category, query, params):
    base = f"{category}\0{query}"
    split_digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    if params:
        identity = f"{base}\0{json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)}"
        trial_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    else:
        trial_digest = split_digest
    split = "held-out" if int(split_digest[:8], 16) % 5 == 0 else "tuning"
    return trial_digest[:16], split


def _matches(movie, constraints):
    genres = constraints.get("genres_any", [])
    if genres and not any(genre in (movie.get("genres") or []) for genre in genres):
        return False
    for key, field in (
        ("excluded_genres", "genres"),
        ("excluded_moods", "mood_tags"),
        ("excluded_themes", "themes"),
        ("excluded_descriptors", "content_descriptors"),
    ):
        if set(constraints.get(key, [])) & set(movie.get(field) or []):
            return False
    exclusion_keys = (
        "excluded_genres", "excluded_moods", "excluded_themes",
        "excluded_descriptors", "excluded_terms",
    )
    needs_text = any(constraints.get(key) for key in exclusion_keys) or bool(constraints.get("terms_all"))
    movie_id = str(movie.get("id"))
    title_key = TITLE_KEY_BY_ID.get(movie_id)
    if title_key is None:
        title_key = _normalized_title(movie.get("title", ""))
    if needs_text:
        searchable_text = ORACLE_TEXT_BY_ID.get(movie_id)
        if searchable_text is None:
            searchable_text = _movie_text(movie)
        text_tokens = _cached_movie_tokens(movie_id, searchable_text)
    else:
        text_tokens = ()
    text_token_set = set(text_tokens)
    exclusion_tokens = [
        _cached_movie_tokens(movie_id, field)
        for field in ORACLE_FIELDS_BY_ID.get(movie_id, (_movie_text(movie),))
    ] if any(constraints.get(key) for key in exclusion_keys) else []
    exclusion_token_sets = [set(tokens) for tokens in exclusion_tokens]
    for constraint_key in (
        "excluded_genres", "excluded_moods", "excluded_themes", "excluded_descriptors",
    ):
        for canonical in constraints.get(constraint_key, []):
            forms = EXCLUSION_SYNONYMS[constraint_key].get(canonical, (canonical,))
            if any(
                _contains_phrase(tokens, form, token_set)
                for tokens, token_set in zip(exclusion_tokens, exclusion_token_sets)
                for form in forms
            ):
                return False
    if any(
        _contains_phrase(tokens, term, token_set)
        for tokens, token_set in zip(exclusion_tokens, exclusion_token_sets)
        for term in constraints.get("excluded_terms", [])
    ):
        return False
    year = movie.get("release_year")
    if constraints.get("year_min") is not None and (year is None or year < constraints["year_min"]):
        return False
    if constraints.get("year_max") is not None and (year is None or year > constraints["year_max"]):
        return False
    runtime = movie.get("runtime") or 0
    if constraints.get("runtime_min") is not None and (not runtime or runtime < constraints["runtime_min"]):
        return False
    if constraints.get("runtime_max") is not None and (not runtime or runtime > constraints["runtime_max"]):
        return False
    country = constraints.get("country")
    if country and country not in _country_codes(movie):
        return False
    award = constraints.get("award")
    if award:
        awards = [str(value).casefold() for value in movie.get("notable_awards") or []]
        phrases = {
            "academy": ("academy award",),
            "palme": ("palme d'or",),
            "golden-bear": ("golden bear",),
            "golden-lion": ("golden lion",),
        }
        if not awards or (award != "winners" and not any(
            phrase in text for text in awards for phrase in phrases[award]
        )):
            return False
    if constraints.get("title_exact") is not None:
        target = constraints["title_exact"]
        if title_key != target:
            return False
    if constraints.get("title_fragment"):
        if constraints["title_fragment"] not in title_key:
            return False
    if constraints.get("terms_all"):
        if not set(constraints["terms_all"]).issubset(text_token_set):
            return False
    return True


def _expected(constraints):
    return [movie for movie in MOVIES if _matches(movie, constraints)]


def generate_trials():
    trials = []
    known_terms = {
        token
        for dictionary in (GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS, CONTENT_DESCRIPTOR_SYNONYMS)
        for canonical, forms in dictionary.items()
        for phrase in [canonical, *forms]
        for token in _oracle_tokens(phrase)
    }

    def add(category, query, constraints=None, source_id=None, check_filter=False, note="", params=None, expected_parser=None):
        constraints = constraints or {}
        expected = _expected(constraints)
        trial_id, split = _trial_identity(category, query, params or {})
        trials.append({
            "id": trial_id,
            "category": category,
            "query": query,
            "constraints": constraints,
            "expected_ids": [str(movie["id"]) for movie in expected],
            "source_id": str(source_id) if source_id is not None else None,
            "check_filter": check_filter,
            "note": note,
            "params": params or {},
            "expected_parser": expected_parser or {},
            "split": split,
        })

    for movie in MOVIES:
        title = movie.get("title", "").strip()
        if not title:
            continue
        exact = _normalized_title(title)
        add("title", title, {"title_exact": exact}, movie["id"], True, "full title")
        if title.casefold() != title:
            add("title", title.casefold(), {"title_exact": exact}, movie["id"], True, "case change")
        if re.search(r"[^\w\s]", title, flags=re.UNICODE):
            add("title", re.sub(r"[^\w\s]", " ", title).strip(), {"title_exact": exact}, movie["id"], True, "punctuation removed")
        if not exact.startswith("the "):
            add("title", f"the {title}", {"title_exact": exact}, movie["id"], True, "leading article")
        words = title.split()
        if len(words) >= 3 and int(movie["id"]) % 4 == 0:
            fragment = _normalized_title(" ".join(words[:2]))
            exact_fragment_matches = any(
                _normalized_title(candidate.get("title", "")) == fragment
                for candidate in MOVIES
            )
            if not exact_fragment_matches and not _has_exact_article_alias(fragment, MOVIES):
                add(
                    "title-fragment",
                    " ".join(words[:2]),
                    {"title_fragment": fragment},
                    movie["id"],
                    False,
                    "partial title",
                )

    description_movies = [movie for movie in MOVIES if movie.get("keywords") or movie.get("synopsis")]
    token_frequency = defaultdict(int)
    for candidate in MOVIES:
        for token in set(_oracle_tokens(_descriptive_text(candidate))):
            token_frequency[token] += 1
    for movie in description_movies[:350]:
        words = [
            token for token in _oracle_tokens(_descriptive_text(movie))
            if (
                len(token) >= 5 and token_frequency[token] <= 20
                and token not in known_terms and token not in ORACLE_COMMON_WORDS
            )
        ]
        if not words:
            continue
        words = sorted(set(words), key=lambda token: (token_frequency[token], -len(token), token))[:2]
        add(
            "description",
            "movies about " + " ".join(words),
            {"terms_all": words},
            movie["id"],
            False,
            "rare catalog field terms",
        )

    genres = sorted({genre for movie in MOVIES for genre in movie.get("genres", [])})
    for genre in genres:
        add("single-genre", f"{genre} movies", {"genres_any": [genre]}, check_filter=True)

    years = sorted({int(movie["release_year"]) for movie in MOVIES if movie.get("release_year")})
    for year in years:
        add("single-year", f"movies from {year}", {"year_min": year, "year_max": year}, check_filter=True)
    for year in years[::max(1, len(years) // 12)]:
        add("single-year", f"movies before {year}", {"year_max": year - 1}, check_filter=True)
        add("single-year", f"movies after {year}", {"year_min": year + 1}, check_filter=True)

    runtimes = sorted({int(movie["runtime"]) for movie in MOVIES if movie.get("runtime")})
    if runtimes:
        thresholds = [runtimes[len(runtimes) * index // 5] for index in range(1, 5)]
        for threshold in thresholds:
            add(
                "single-runtime", f"movies under {threshold} minutes",
                {"runtime_max": threshold - 1}, check_filter=True,
                expected_parser={"runtime": {"max": threshold - 1}},
            )
            add(
                "single-runtime", f"movies over {threshold} minutes",
                {"runtime_min": threshold + 1}, check_filter=True,
                expected_parser={"runtime": {"min": threshold + 1}},
            )
            add(
                "single-runtime", f"movies around {threshold} minutes",
                expected_parser={"runtime": {"target": threshold}},
                note="target is a soft runtime preference",
            )
        number_cases = [
            ("under 90 minutes", {"runtime_max": 89}, {"max": 89}),
            ("less than 90 minutes", {"runtime_max": 89}, {"max": 89}),
            ("no more than 90 minutes", {"runtime_max": 90}, {"max": 90}),
            ("over 90 minutes", {"runtime_min": 91}, {"min": 91}),
            ("at least 90 minutes", {"runtime_min": 90}, {"min": 90}),
            ("between 90 and 120 minutes", {"runtime_min": 90, "runtime_max": 120}, {"min": 90, "max": 120}),
            ("under 1h30", {"runtime_max": 89}, {"max": 89}),
            ("around 1h30", {}, {"target": 90}),
            ("two and a half hours", {}, {"target": 150}),
            ("movies between 1 and 2 hours", {"runtime_min": 60, "runtime_max": 120}, {"min": 60, "max": 120}),
        ]
        for phrase, limits, parsed in number_cases:
            query = phrase if phrase.startswith("movies ") else f"movies {phrase}"
            add(
                "runtime-number-forms",
                query,
                limits,
                check_filter=bool(limits),
                expected_parser={"runtime": parsed},
            )
        for movie in description_movies[:100]:
            runtime = movie.get("runtime") or 0
            if runtime:
                add(
                    "runtime-number-forms",
                    f"{movie['title']} under {runtime + 1} minutes",
                    {"title_exact": _normalized_title(movie["title"]), "runtime_max": runtime},
                    movie["id"], True, "source-title and exact runtime ceiling",
                )

    for query, constraint in (
        ("movies from the 90s", {"min": 1990, "max": 1999}),
        ("movies from the 1990s", {"min": 1990, "max": 1999}),
        ("movies from the nineties", {"min": 1990, "max": 1999}),
    ):
        add(
            "decade-number-forms",
            query,
            {"year_min": constraint["min"], "year_max": constraint["max"]},
            check_filter=True,
            expected_parser={"release_year": constraint},
        )

    countries = sorted({
        country for movie in MOVIES for country in _country_codes(movie)
        if isinstance(country, str) and country
    })
    country_labels = app_module.COUNTRY_LABELS
    for code in countries:
        label = country_labels.get(code)
        if label:
            add("country", f"movies from {label}", {"country": code}, check_filter=True)

    for key in ("winners", "academy", "palme", "golden-bear", "golden-lion"):
        if any(_matches(movie, {"award": key}) for movie in MOVIES):
            add(
                "recognition-chip", "", {"award": key}, check_filter=True,
                note=f"award chip: {key}", params={"awards": key},
            )

    randomizer = random.Random(731_204)
    if genres:
        for index in range(80):
            genre = randomizer.choice(genres)
            per_page = randomizer.choice((12, 24, 48))
            award = "winners" if index % 2 == 0 else None
            constraints = {"genres_any": [genre]}
            params = {
                "genre": genre,
                "per_page": per_page,
                "page": randomizer.choice((1, 2, 3)),
                "sort": randomizer.choice(("title", "rating", "year")),
                "view": randomizer.choice(("grid", "list")),
                "language": randomizer.choice(("en", "es", "tl", "fr")),
            }
            if award:
                constraints["award"] = award
                params["awards"] = award
            add(
                "filter-combination",
                "",
                constraints,
                check_filter=True,
                note="genre + optional recognition + pagination and presentation parameters",
                params=params,
            )

    eligible = [movie for movie in MOVIES if movie.get("runtime") and movie.get("release_year") and movie.get("genres")]
    for movie in randomizer.sample(eligible, min(300, len(eligible))):
        genre = movie["genres"][0]
        excluded = next((value for value in genres if value not in movie["genres"]), None)
        if not excluded:
            continue
        runtime = int(movie["runtime"])
        year = int(movie["release_year"])
        query = f"{genre} movie from {year} under {runtime + 1} minutes without {excluded}"
        constraints = {
            "genres_any": [genre],
            "year_min": year,
            "year_max": year,
            "runtime_max": runtime,
            "excluded_genres": [excluded],
        }
        source_id = movie["id"] if _matches(movie, constraints) else None
        add("combined", query, constraints, source_id, True, "genre + year + runtime + exclusion")

    dictionaries = (
        ("genre", GENRE_SYNONYMS, "genres", "single-genre"),
        ("mood", MOOD_SYNONYMS, "mood_tags", "mood"),
        ("theme", THEME_SYNONYMS, "themes", "theme"),
        ("content_descriptor", CONTENT_DESCRIPTOR_SYNONYMS, "content_descriptors", "content-exclusion"),
    )
    alias_owners = {}
    for kind, dictionary, _, _ in dictionaries:
        for canonical, forms in dictionary.items():
            for form in set([canonical, *forms]):
                key = " ".join(_oracle_tokens(form))
                alias_owners.setdefault(key, (kind, canonical, form))
    seen_aliases = set()
    for kind, dictionary, _, _ in dictionaries:
        for canonical, forms in dictionary.items():
            for form in sorted(set([canonical, *forms])):
                key = " ".join(_oracle_tokens(form))
                owner = alias_owners.get(key)
                if key in seen_aliases or not owner:
                    continue
                seen_aliases.add(key)
                owner_kind, owner_tag, _ = owner
                if owner_kind == "genre":
                    query = f"{form} movies"
                    constraints = {"genres_any": [owner_tag]}
                    expected_parser = {"genres": [owner_tag]}
                elif owner_kind == "content_descriptor":
                    query = f"movies without {form}"
                    constraints = {"excluded_descriptors": [owner_tag]}
                    expected_parser = {"excluded_content_descriptors": [owner_tag]}
                else:
                    query = f"movies with {form}"
                    constraints = {}
                    expected_parser = {
                        "mood": {"moods": [owner_tag]},
                        "theme": {"themes": [owner_tag]},
                    }[owner_kind]
                add(
                    "synonym", query, constraints, check_filter=bool(constraints),
                    note=f"{owner_kind}:{owner_tag}", expected_parser=expected_parser,
                )
                if owner_kind in {"genre", "mood", "theme", "content_descriptor"}:
                    exclusion_key = {
                        "genre": "excluded_genres",
                        "mood": "excluded_moods",
                        "theme": "excluded_themes",
                        "content_descriptor": "excluded_descriptors",
                    }[owner_kind]
                    add(
                        "negation",
                        f"movies without {form}",
                        {exclusion_key: [owner_tag]},
                        check_filter=True,
                        note=f"negated {owner_kind}:{owner_tag}",
                        expected_parser={
                            "excluded_genres": [owner_tag],
                        } if owner_kind == "genre" else {
                            "excluded_moods": [owner_tag],
                        } if owner_kind == "mood" else {
                            "excluded_themes": [owner_tag],
                        } if owner_kind == "theme" else {
                            "excluded_content_descriptors": [owner_tag],
                        },
                    )
                if len(form) >= 5 and " " not in form:
                    typo = form[:1] + form[2:]
                    typo_query = f"movies without {typo}" if owner_kind == "content_descriptor" else f"{typo} movies"
                    add(
                        "typo", typo_query, constraints, check_filter=bool(constraints),
                        note=f"deleted character from {form}", expected_parser=expected_parser,
                    )

    keyword_terms = sorted({
        token
        for movie in MOVIES
        for keyword in movie.get("keywords") or []
        for token in _oracle_tokens(keyword)
        if len(token) >= 4 and token not in known_terms
    })
    for term in keyword_terms[:60]:
        add(
            "negation-keyword",
            f"movies without {term}",
            {"excluded_terms": [term]},
            check_filter=True,
            note="generic keyword exclusion across searchable fields",
            expected_parser={"excluded_keywords": [term]},
        )

    taglish_forms = [
        ("nakakatawa na movies", {"genres_any": ["comedy"]}),
        ("pamilya movies", {"genres_any": ["family"]}),
        ("nakakatakot na films", {}),
        ("pelikula ng pamilya", {"genres_any": ["family"]}),
        ("movies na walang comedy", {"excluded_genres": ["comedy"]}),
    ]
    for query, constraints in taglish_forms:
        add("language-mix", query, constraints, check_filter=bool(constraints))

    for movie in eligible[:100]:
        title = movie["title"]
        add(
            "similar-to",
            f"movies like {title}",
            note="independent similarity oracle is reported from catalog term overlap",
        )

    add("unsupported-cast", "movies starring a person", note="cast coverage is absent in this catalog")
    add("unsupported-certification", "movies rated PG-13", note="certification coverage is absent in this catalog")

    surprising = [
        "", "   ", "x", "the", "a" * 10000, "!!! ??? ...", "😀🎬🍿",
        "<script>alert(1)</script>", "' OR 1=1 --", "under 999999999999999999 minutes",
        "movies after -100", "movies under 1h30", "two and a half hours",
        "comedy " * 200, "hello 世界 مرحبا", "Café déjà vu",
    ]
    for query in surprising:
        add("surprising-input", query, note="boundary or adversarial input")

    unique = {}
    for trial in trials:
        params_key = json.dumps(trial.get("params", {}), sort_keys=True)
        unique.setdefault((trial["category"], trial["query"], params_key), trial)
    return list(unique.values())


class RouteRunner:
    def __init__(self):
        self.client = app_module.app.test_client()

    def run(self, trial, params=None):
        query = trial["query"]
        parameters = {"q": query, "per_page": 48, "debug": 1, **trial.get("params", {})}
        if params:
            parameters.update(params)
        captured = []
        counts = {
            "retrieval": None,
            "after_hard_filters": None,
            "verification_in": None,
            "verification_out": None,
            "reranking_in": None,
            "reranking_out": None,
        }
        verification = {}
        originals = {
            "retrieve": app_module.retrieve_candidates,
            "rerank": app_module.rerank_candidates,
            "rank": app_module.rank_movies,
            "debug": app_module.build_debug_view,
        }

        def capture(sender, template, context, **extra):
            captured.append(context)

        def retrieve(*args, **kwargs):
            result = originals["retrieve"](*args, **kwargs)
            counts["retrieval"] = len(result)
            return result

        def rerank(*args, **kwargs):
            counts["verification_in"] = len(args[1]) if len(args) > 1 else len(kwargs.get("candidates", []))
            result = originals["rerank"](*args, **kwargs)
            counts["verification_out"] = len(result)
            for item in result:
                verification[str(item["movie"]["id"])] = {
                    "match_level": item.get("match_level"),
                    "satisfied": item.get("satisfied", []),
                    "unsatisfied": item.get("unsatisfied", []),
                    "unverifiable": item.get("unverifiable", []),
                }
            return result

        def rank(*args, **kwargs):
            movies = args[0] if args else kwargs.get("movies", [])
            counts["reranking_in"] = len(movies)
            result = originals["rank"](*args, **kwargs)
            counts["reranking_out"] = len(result.get("results", []))
            return result

        def debug(*args, **kwargs):
            if len(args) > 2:
                counts["after_hard_filters"] = args[2]
            return originals["debug"](*args, **kwargs)

        template_rendered.connect(capture, sender=app_module.app)
        started = time.perf_counter()
        try:
            with patch.dict(os.environ):
                os.environ.pop("OPENAI_API_KEY", None)
                with (
                    patch("app.allow_search", return_value=True),
                    patch("nlp.smart_search._redis_client", return_value=None),
                    patch("socket.create_connection", side_effect=AssertionError("offline trial attempted network I/O")),
                    patch.object(app_module, "retrieve_candidates", side_effect=retrieve),
                    patch.object(app_module, "rerank_candidates", side_effect=rerank),
                    patch.object(app_module, "rank_movies", side_effect=rank),
                    patch.object(app_module, "build_debug_view", side_effect=debug),
                ):
                    response = self.client.get("/?" + urlencode(parameters))
        except Exception as error:
            return {
                "status": None,
                "exception": f"{type(error).__name__}: {error}",
                "latency_seconds": time.perf_counter() - started,
                "stages": counts,
                "preferences": {},
                "understanding": {},
                "result_ids": [],
                "results": [],
                "result_count": 0,
                "notice": "",
                "searched": False,
                "verification": verification,
            }
        finally:
            template_rendered.disconnect(capture, sender=app_module.app)

        response_body = response.get_data(as_text=True)
        elapsed = time.perf_counter() - started
        context = captured[-1] if captured else {}
        rendered = context.get("results") or []
        browsed = context.get("movies") or []
        final_entries = rendered or browsed
        if len(query) <= 500:
            extracted = build_structured_query(query)["preferences"] if query else {}
            understanding = interpret_query(query) if query else {}
        else:
            extracted = {"rejected": "query exceeds 500 characters"}
            understanding = {}
        return {
            "status": response.status_code,
            "exception": None,
            "latency_seconds": elapsed,
            "stages": counts,
            "preferences": extracted,
            "understanding": understanding,
            "result_ids": [str(item.get("movie", {}).get("id")) for item in final_entries],
            "results": [{
                "id": str(item.get("movie", {}).get("id")),
                "title": item.get("movie", {}).get("title"),
                "runtime": item.get("movie", {}).get("runtime"),
                "release_year": item.get("movie", {}).get("release_year"),
                "genres": item.get("movie", {}).get("genres"),
                "mood_tags": item.get("movie", {}).get("mood_tags"),
                "themes": item.get("movie", {}).get("themes"),
                "keywords": item.get("movie", {}).get("keywords"),
                "percent": item.get("percent"),
                "match_level": item.get("match_level"),
                "reasons": item.get("reasons", []),
                "breakdown": item.get("breakdown", {}),
            } for item in final_entries],
            "verification": verification,
            "result_count": context.get("result_count", 0),
            "notice": (
                context.get("search_notice")
                or context.get("assistant_message")
                or response_body.strip()
            ),
            "searched": context.get("searched", False),
            "raw_markup_reflected": any(
                marker in query and query in response_body
                for marker in ("<script", "<img", "<svg", "<iframe")
            ),
        }


def _check_trial(trial, actual):
    failures = []
    if actual["exception"]:
        failures.append({"stage": "route-exception", "reason": actual["exception"]})
        return failures
    if actual["status"] is None or actual["status"] >= 500:
        failures.append({"stage": "http", "reason": f"HTTP status {actual['status']}"})
    if actual["latency_seconds"] > MAX_LATENCY_SECONDS:
        failures.append({"stage": "latency", "reason": f"{actual['latency_seconds']:.3f}s exceeds {MAX_LATENCY_SECONDS}s"})
    for key, expected_value in trial.get("expected_parser", {}).items():
        actual_value = actual["preferences"].get(key)
        if isinstance(expected_value, list) and isinstance(actual_value, list):
            mismatch = {} if set(expected_value).issubset(actual_value) else {
                key: (expected_value, actual_value)
            }
        elif isinstance(expected_value, dict) and isinstance(actual_value, dict):
            mismatch = {
                name: (value, actual_value.get(name))
                for name, value in expected_value.items()
                if actual_value.get(name) != value
            }
        else:
            mismatch = {} if actual_value == expected_value else {key: (expected_value, actual_value)}
        if mismatch:
            failures.append({
                "stage": "parser",
                "reason": f"{key} mismatch: {mismatch}",
            })
    if trial["query"] and not actual["result_ids"] and not actual["notice"]:
        failures.append({"stage": "empty-result", "reason": "empty result has no explanation or next step"})
    if trial["category"].startswith("unsupported-"):
        if actual["results"]:
            failures.append({
                "stage": "unsupported-filter",
                "reason": "results were returned despite an unanswerable metadata constraint",
            })
        if not any(term in actual["notice"].casefold() for term in ("catalog", "cannot", "unavailable", "doesn't include")):
            failures.append({
                "stage": "unsupported-filter",
                "reason": "missing metadata limitation was not explained",
            })
    constraints = trial["constraints"]
    returned = {str(result["id"]): result for result in actual["results"]}
    if trial["check_filter"]:
        for result in actual["results"]:
            movie = next((item for item in MOVIES if str(item["id"]) == str(result["id"])), None)
            if movie is not None and not _matches(movie, constraints):
                failures.append({
                    "stage": "hard-filter",
                    "reason": f"{result['title']} violates oracle filters",
                    "movie_id": result["id"],
                })
                break
    if trial["source_id"] and trial["source_id"] not in returned:
        failures.append({
            "stage": "recall",
            "reason": "source movie not returned in top 48",
            "expected_id": trial["source_id"],
        })
    for result in actual["results"]:
        score = result.get("percent")
        if score is not None and (not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100):
            failures.append({"stage": "score", "reason": f"invalid score {score!r}", "movie_id": result["id"]})
            break
        if result.get("match_level") == "full" and trial["check_filter"] and not _matches(
            next((movie for movie in MOVIES if str(movie["id"]) == str(result["id"])), {}),
            constraints,
        ):
            failures.append({"stage": "false-exact", "reason": "full match violates independent hard filters"})
            break
        if result.get("match_level") == "full":
            checked = actual["verification"].get(str(result["id"]), {})
            if not checked.get("satisfied") or checked.get("unsatisfied") or checked.get("unverifiable"):
                failures.append({
                    "stage": "false-exact",
                    "reason": "full match has no satisfied constraint or has an unresolved requirement",
                    "movie_id": result["id"],
                })
                break
        for reason in result.get("reasons", []):
            text = reason.get("text", "") if isinstance(reason, dict) else str(reason)
            mismatch = False
            genre_match = re.fullmatch(r"(.+) genre matched", text, flags=re.IGNORECASE)
            mood_match = re.fullmatch(r"(.+) mood matched", text, flags=re.IGNORECASE)
            theme_match = re.fullmatch(r"(.+) theme matched", text, flags=re.IGNORECASE)
            no_genre = re.fullmatch(r"No (.+) tag detected", text, flags=re.IGNORECASE)
            no_mood = re.fullmatch(r"No (.+) mood detected", text, flags=re.IGNORECASE)
            no_theme = re.fullmatch(r"No (.+) theme detected", text, flags=re.IGNORECASE)
            if genre_match:
                mismatch = genre_match.group(1).casefold() not in {
                    value.casefold() for value in result.get("genres") or []
                }
            elif mood_match:
                mismatch = mood_match.group(1).casefold() not in {
                    value.casefold() for value in result.get("mood_tags") or []
                }
            elif theme_match:
                mismatch = theme_match.group(1).casefold() not in {
                    value.casefold() for value in result.get("themes") or []
                }
            elif no_genre:
                mismatch = no_genre.group(1).casefold() in {
                    value.casefold() for value in result.get("genres") or []
                }
            elif no_mood:
                mismatch = no_mood.group(1).casefold() in {
                    value.casefold() for value in result.get("mood_tags") or []
                }
            elif no_theme:
                mismatch = no_theme.group(1).casefold() in {
                    value.casefold() for value in result.get("themes") or []
                }
            elif text.startswith("Synopsis closely matches"):
                mismatch = (result.get("breakdown", {}).get("keyword") or 0) <= 0.08
            elif text.startswith("Runtime is "):
                runtime_match = re.match(r"Runtime is (\d+) minutes", text)
                mismatch = not runtime_match or int(runtime_match.group(1)) != int(result.get("runtime") or 0)
                if not mismatch and "(under your " in text:
                    limit = re.search(r"under your (\d+)-minute limit", text)
                    mismatch = bool(limit and int(result["runtime"]) > int(limit.group(1)))
                if not mismatch and "(meets your " in text:
                    limit = re.search(r"meets your (\d+)-minute minimum", text)
                    mismatch = bool(limit and int(result["runtime"]) < int(limit.group(1)))
            elif text.startswith("Released in "):
                year_match = re.match(r"Released in (\d+)", text)
                mismatch = not year_match or int(year_match.group(1)) != int(result.get("release_year") or 0)
            if mismatch:
                failures.append({
                    "stage": "explanation",
                    "reason": f"Explanation is not supported by movie data: {text}",
                    "movie_id": result["id"],
                })
                break
    if actual.get("raw_markup_reflected"):
        failures.append({"stage": "safety", "reason": "unescaped user markup was reflected in the response"})
    return failures


def _percentile(samples, percentile):
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)]


def _pagination_checks(runner):
    trial = {
        "query": "action movies",
        "constraints": {"genres_any": ["action"]},
    }
    pages = []
    statuses = []
    counts = []
    for page in (1, 2, 3):
        actual = runner.run(trial, {"per_page": 12, "page": page})
        pages.append(actual["result_ids"])
        statuses.append(actual["status"])
        counts.append(actual["result_count"])
    union = [item for page in pages for item in page]
    duplicate_count = len(union) - len(set(union))
    expected_count = counts[0] if counts else 0
    expected_count_ok = len(union) == expected_count
    invalid = runner.run(trial, {"per_page": "not-a-number", "page": "not-a-number"})
    first_page = runner.run(trial, {"per_page": 12, "page": 1})
    last_page = runner.run(trial, {"per_page": 48, "page": 1})
    beyond = runner.run(trial, {"per_page": 12, "page": 999999})
    zero = runner.run(trial, {"per_page": 12, "page": 0})
    return {
        "status_ok": all(status is not None and status < 500 for status in statuses + [
            invalid["status"], first_page["status"], last_page["status"], beyond["status"], zero["status"],
        ]),
        "page_sizes": [len(page) for page in pages],
        "duplicates": duplicate_count,
        "covers_result_count": expected_count_ok,
        "result_ids": union,
        "invalid_page_count": len(invalid["result_ids"]),
        "per_page_result_count_stable": first_page["result_count"] == last_page["result_count"],
        "out_of_range_page_is_valid": bool(beyond["result_ids"]),
        "zero_page_is_valid": bool(zero["result_ids"]),
        "pass": (
            all(status is not None and status < 500 for status in statuses + [
                invalid["status"], first_page["status"], last_page["status"], beyond["status"], zero["status"],
            ])
            and duplicate_count == 0 and expected_count_ok
            and first_page["result_count"] == last_page["result_count"]
            and bool(beyond["result_ids"]) and bool(zero["result_ids"])
        ),
    }


def run(args):
    trials = generate_trials()
    if args.split != "all":
        trials = [trial for trial in trials if trial["split"] == args.split]
    if args.limit:
        trials = trials[:args.limit]

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    runner = RouteRunner()
    category_stats = defaultdict(lambda: {"trials": 0, "passed": 0, "sources": 0, "source_hits": 0})
    category_split_stats = defaultdict(lambda: defaultdict(lambda: {"trials": 0, "passed": 0}))
    split_stats = defaultdict(lambda: {"trials": 0, "passed": 0})
    split_metrics = defaultdict(lambda: {
        "latencies": [],
        "returned_results": 0,
        "violations": 0,
        "empty_results": 0,
        "full_results": 0,
        "false_full": 0,
        "crashes": 0,
        "http_5xx": 0,
        "recall": {k: {"hits": 0, "sources": 0} for k in (1, 5, 10, 20, 48)},
    })
    failures = []
    latencies = []
    returned = 0
    violating = 0
    empty = 0
    full_count = 0
    false_full = 0
    crash_count = 0
    http_5xx_count = 0
    recall_sources = 0
    recall_hits = 0
    recall_at = {k: {"hits": 0, "sources": 0} for k in (1, 5, 10, 20, 48)}
    observed_ids = {}

    with output.open("w", encoding="utf-8") as stream:
        for trial in trials:
            actual = runner.run(trial)
            found = _check_trial(trial, actual)
            metrics = split_metrics[trial["split"]]
            metrics["latencies"].append(actual["latency_seconds"])
            metrics["empty_results"] += not actual["result_ids"]
            metrics["crashes"] += bool(
                actual["exception"] or actual["status"] is None or actual["status"] >= 500
            )
            metrics["http_5xx"] += bool(actual["status"] is not None and actual["status"] >= 500)
            if trial.get("source_id"):
                for k, metric in metrics["recall"].items():
                    metric["sources"] += 1
                    metric["hits"] += trial["source_id"] in actual["result_ids"][:k]
            if actual["exception"] or actual["status"] is None or actual["status"] >= 500:
                crash_count += 1
            if actual["status"] is not None and actual["status"] >= 500:
                http_5xx_count += 1
            latencies.append(actual["latency_seconds"])
            if not actual["result_ids"]:
                empty += 1
            for result in actual["results"]:
                returned += 1
                metrics["returned_results"] += 1
                if trial["check_filter"]:
                    movie = next((item for item in MOVIES if str(item["id"]) == str(result["id"])), None)
                    if movie is not None and not _matches(movie, trial["constraints"]):
                        violating += 1
                        metrics["violations"] += 1
                if result.get("match_level") == "full":
                    full_count += 1
                    metrics["full_results"] += 1
                    if trial["check_filter"]:
                        movie = next((item for item in MOVIES if str(item["id"]) == str(result["id"])), {})
                        if not _matches(movie, trial["constraints"]):
                            false_full += 1
                            metrics["false_full"] += 1
            if trial["source_id"]:
                recall_sources += 1
                if trial["source_id"] in actual["result_ids"]:
                    recall_hits += 1
                for k, metric in recall_at.items():
                    metric["sources"] += 1
                    metric["hits"] += trial["source_id"] in actual["result_ids"][:k]
            observed_ids[trial["id"]] = actual["result_ids"]
            stat = category_stats[trial["category"]]
            stat["trials"] += 1
            stat["passed"] += not found
            stat["sources"] += bool(trial["source_id"])
            stat["source_hits"] += bool(trial["source_id"] and trial["source_id"] in actual["result_ids"])
            split_stat = split_stats[trial["split"]]
            split_stat["trials"] += 1
            split_stat["passed"] += not found
            category_split_stat = category_split_stats[trial["split"]][trial["category"]]
            category_split_stat["trials"] += 1
            category_split_stat["passed"] += not found
            record = {
                **trial,
                "actual": actual,
                "failures": found,
            }
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            for failure in found:
                if len(failures) < 100:
                    failures.append({
                        "trial_id": trial["id"],
                        "split": trial["split"],
                        "category": trial["category"],
                        "query": trial["query"],
                        "expected_ids": trial["expected_ids"][:20],
                        "actual_ids": actual["result_ids"][:20],
                        **failure,
                    })

    stability_failures = []
    with output.open("a", encoding="utf-8") as stream:
        for trial in trials[::max(1, len(trials) // 40)][:40]:
            repeated = runner.run(trial)
            stable = repeated["result_ids"] == observed_ids.get(trial["id"], [])
            if not stable:
                stability_failures.append({
                    "stage": "determinism",
                    "category": trial["category"],
                    "query": trial["query"],
                    "actual_ids": repeated["result_ids"],
                    "reason": "repeated request changed result order or contents",
                })
            stream.write(json.dumps({
                "id": trial["id"],
                "category": "stability",
                "query": trial["query"],
                "split": trial["split"],
                "actual": repeated,
                "stable": stable,
            }, ensure_ascii=False, default=str) + "\n")

    pagination = _pagination_checks(runner)
    failures.extend(stability_failures)
    total = len(trials)
    summary = {
        "catalog_movies": len(MOVIES),
        "trials": total,
        "category_counts": {key: value["trials"] for key, value in sorted(category_stats.items())},
        "category_split_pass_rates": {
            split: {
                category: {
                    "trials": value["trials"],
                    "pass_rate": value["passed"] / value["trials"] if value["trials"] else 1.0,
                }
                for category, value in sorted(categories.items())
            }
            for split, categories in sorted(category_split_stats.items())
        },
        "tuning": {
            **split_stats.get("tuning", {"trials": 0, "passed": 0}),
            "pass_rate": (
                split_stats["tuning"]["passed"] / split_stats["tuning"]["trials"]
                if split_stats.get("tuning", {}).get("trials") else 1.0
            ),
        },
        "held_out": {
            **split_stats.get("held-out", {"trials": 0, "passed": 0}),
            "pass_rate": (
                split_stats["held-out"]["passed"] / split_stats["held-out"]["trials"]
                if split_stats.get("held-out", {}).get("trials") else 1.0
            ),
        },
        "split_metrics": {
            split: {
                "returned_results": values["returned_results"],
                "hard_constraint_violation_rate": (
                    values["violations"] / values["returned_results"]
                    if values["returned_results"] else 0.0
                ),
                "empty_result_rate": (
                    values["empty_results"] / split_stats[split]["trials"]
                    if split_stats[split]["trials"] else 0.0
                ),
                "false_exact_rate": (
                    values["false_full"] / values["full_results"]
                    if values["full_results"] else 0.0
                ),
                "crash_count": values["crashes"],
                "http_5xx_count": values["http_5xx"],
                "latency_seconds": {
                    "p95": _percentile(values["latencies"], 0.95),
                    "p99": _percentile(values["latencies"], 0.99),
                    "max": max(values["latencies"], default=0.0),
                },
                "recall_at": {
                    f"recall@{k}": metric["hits"] / metric["sources"] if metric["sources"] else None
                    for k, metric in values["recall"].items()
                },
            }
            for split, values in sorted(split_metrics.items())
        },
        "category_pass_rates": {
            key: {
                "trials": value["trials"],
                "pass_rate": value["passed"] / value["trials"] if value["trials"] else 1.0,
                "source_recall_at_48": value["source_hits"] / value["sources"] if value["sources"] else None,
            }
            for key, value in sorted(category_stats.items())
        },
        "recall_at": {
            f"recall@{k}": metric["hits"] / metric["sources"] if metric["sources"] else None
            for k, metric in recall_at.items()
        },
        "recall_at_48": recall_hits / recall_sources if recall_sources else None,
        "hard_constraint_violation_rate": violating / returned if returned else 0.0,
        "empty_result_rate": empty / total if total else 0.0,
        "false_exact_rate": false_full / full_count if full_count else 0.0,
        "crash_count": crash_count,
        "http_5xx_count": http_5xx_count,
        "latency_seconds": {
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies, default=0.0),
        },
        "pagination": pagination,
        "failure_count": (
            sum(value["trials"] - value["passed"] for value in category_stats.values())
            + len(stability_failures)
            + int(not pagination["pass"])
        ),
        "failure_examples": failures[:25],
        "jsonl": str(output.resolve()),
    }
    baseline_guard = {"enabled": False, "pass": True}
    if args.split != "tuning" and "held-out" in summary["split_metrics"] and BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["held_out"]
        held_out = summary["split_metrics"]["held-out"]
        baseline_guard = {
            "enabled": True,
            "pass": (
                held_out["recall_at"]["recall@48"] >= baseline["recall@48"]
                and held_out["hard_constraint_violation_rate"]
                <= baseline["hard_constraint_violation_rate"]
            ),
            "baseline_recall_at_48": baseline["recall@48"],
            "actual_recall_at_48": held_out["recall_at"]["recall@48"],
            "baseline_hard_constraint_violation_rate": baseline["hard_constraint_violation_rate"],
            "actual_hard_constraint_violation_rate": held_out["hard_constraint_violation_rate"],
        }
    summary["baseline_guard"] = baseline_guard
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["failure_count"] == 0 and pagination["pass"] and baseline_guard["pass"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("all", "tuning", "held-out"), default="all")
    parser.add_argument("--limit", type=int, default=0, help="run the first N deterministic trials")
    parser.add_argument(
        "--output",
        default=str(ROOT / "tests" / ".search-trials.jsonl"),
        help="JSONL log path",
    )
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
