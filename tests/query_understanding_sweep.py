"""Catalog-generated query checks and deterministic tuning/held-out rates."""

import hashlib
import json
import re
import sys
from itertools import combinations
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)
from nlp.catalog_matching import normalize_title
from nlp.extract import extract_preferences
from nlp.normalize import normalize_text
from nlp.query import build_structured_query, detect_country
from nlp.runtime import extract_runtime_constraint
from nlp.tokenize import STOPWORDS
from nlp.smart_search import _filter_excluded_movies

try:
    from nlp.runtime import WORD_NUMBERS
except ImportError:
    WORD_NUMBERS = {
        "zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
        "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
        "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    }


def _risky_titles(movies):
    vocabulary = set()
    for table in (
        GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS,
        CONTENT_DESCRIPTOR_SYNONYMS,
    ):
        for canonical, forms in table.items():
            vocabulary.update(normalize_title(form) for form in (canonical, *forms))
    vocabulary.update({
        "short", "quick", "long", "hour", "hours", "minute", "minutes",
        "year", "years", "half", "hundred", "thirty", "forty", "fifty",
        "sixty", "seventy", "eighty", "ninety", *WORD_NUMBERS,
        "best", "top", "highest", "rated", "greatest", "worst", "lowest",
    })
    vocabulary_words = {word for phrase in vocabulary for word in phrase.split()}
    return {
        normalize_title(movie.get("title", ""))
        for movie in movies
        if set(normalize_title(movie.get("title", "")).split()).intersection(vocabulary_words)
        or any(word.isdigit() for word in normalize_title(movie.get("title", "")).split())
    }


def _case(category, query, passed):
    split = "held_out" if int(hashlib.sha256(
        f"{category}:{query}".encode("utf-8")
    ).hexdigest()[:2], 16) % 5 == 0 else "tuning"
    return {"category": category, "query": query, "split": split, "passed": bool(passed)}


def _catalog_genres():
    return sorted({
        normalize_text(genre).replace(" , ", " ").strip()
        for movie in app.MOVIES
        for genre in movie.get("genres", [])
        if isinstance(genre, str) and normalize_text(genre)
    })


def _oracle_forms(tag):
    forms = {normalize_title(tag)}
    tables = (
        GENRE_SYNONYMS, MOOD_SYNONYMS, THEME_SYNONYMS,
        CONTENT_DESCRIPTOR_SYNONYMS,
    )
    changed = True
    while changed:
        changed = False
        for table in tables:
            for canonical, synonyms in table.items():
                linked = {
                    normalize_title(value)
                    for value in (canonical, *synonyms)
                    if normalize_title(value)
                }
                if forms.intersection(linked) and not linked.issubset(forms):
                    forms.update(linked)
                    changed = True
    for phrase in tuple(forms):
        variants = [""]
        for word in phrase.split():
            word_forms = {word}
            if len(word) > 4 and word.endswith("ies"):
                word_forms.add(word[:-3] + "y")
            elif len(word) > 3 and word.endswith("y"):
                word_forms.add(word[:-1] + "ies")
                word_forms.add(word + "s")
            elif len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
                word_forms.add(word[:-2])
            elif len(word) > 3 and word.endswith("s"):
                word_forms.add(word[:-1])
            elif len(word) > 2:
                word_forms.add(word + "s")
                if word.endswith("y"):
                    word_forms.add(word[:-1] + "ies")
            variants = [
                f"{prefix} {word_form}".strip()
                for prefix in variants
                for word_form in word_forms
            ]
        forms.update(variants)
    return forms


def _oracle_excludes(movie, excluded_genres, forms_by_genre):
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
            fields.extend(
                item if isinstance(item, str) else " ".join(
                    str(child) for child in item.values() if isinstance(child, str)
                ) if isinstance(item, dict) else ""
                for item in value
            )
    haystack = " " + " ".join(normalize_title(value) for value in fields if value) + " "
    return any(
        f" {form} " in haystack
        for tag in excluded_genres
        for form in forms_by_genre[tag]
    )


def _genre_exclusion_filter_cases():
    genres = _catalog_genres()
    forms_by_genre = {genre: _oracle_forms(genre) for genre in genres}
    exclusion_sets = [
        excluded
        for size in (1, 2, 3)
        for excluded in combinations(genres, size)
    ]
    filtered_ids = {}
    oracle_ids = {}
    gate_clear = {}
    for genre in genres:
        preferences = extract_preferences(f"not {genre}")
        oracle_ids[genre] = {
            str(movie.get("id"))
            for movie in app.MOVIES
            if not _oracle_excludes(movie, (genre,), forms_by_genre)
        }
        candidates = _filter_excluded_movies(
            app.MOVIES, {"filters": preferences},
        )
        filtered_ids[genre] = {str(movie.get("id")) for movie in candidates}
        gate_clear[genre] = all(
            not app._final_gate_violations(movie, preferences)
            for movie in candidates
        )
    cases = []
    for excluded in exclusion_sets:
        query = ", ".join(f"not {genre}" for genre in excluded)
        preferences = extract_preferences(query)
        expected = set.intersection(*(oracle_ids[genre] for genre in excluded))
        actual = set.intersection(*(filtered_ids[genre] for genre in excluded))
        cases.append(_case(
            "genre_exclusion_filter",
            query,
            set(excluded).issubset(preferences["excluded_genres"])
            and actual == expected
            and all(gate_clear[genre] for genre in excluded),
        ))
    return cases


def _query_rules():
    loader = getattr(app, "load_query_rules", None)
    if loader:
        return loader()
    return json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "query_rules.json")
        .read_text(encoding="utf-8")
    )


def generate_cases():
    cases = []
    seen = set()
    query_rules = _query_rules()
    risky_titles = _risky_titles(app.MOVIES)
    for movie in app.MOVIES:
        title = movie.get("title", "")
        normalized = normalize_title(title)
        if not title or (normalized, "title_collision") in seen:
            continue
        if normalized in risky_titles:
            matches = app.find_title_matches(title, app.MOVIES)
            cases.append(_case(
                "title_collision", title,
                any(normalize_title(item.get("title", "")) == normalized for item in matches),
            ))
            seen.add((normalized, "title_collision"))
        compact = normalized.replace(" ", "")
        if len(compact) <= 3 or normalized.isdigit():
            matches = app.find_title_matches(title, app.MOVIES)
            cases.append(_case(
                "short_or_numeric_title", title,
                any(normalize_title(item.get("title", "")) == normalized for item in matches),
            ))
        if re.search(r"\d", title):
            matches = app.find_title_matches(title, app.MOVIES)
            cases.append(_case(
                "digit_title", title,
                any(normalize_title(item.get("title", "")) == normalized for item in matches),
            ))

    for movie in app.MOVIES:
        title = movie.get("title", "")
        normalized = normalize_title(title)
        if not title:
            continue
        compact = normalized.replace(" ", "")
        if (
            len(compact) <= 3
            or normalized.isdigit()
            or (normalized.split() and all(word in STOPWORDS for word in normalized.split()))
        ):
            embedded = f"I want to watch {title} again"
            quoted = f'I want to watch "{title}" again'
            named = f"I want a movie called {title}"
            cases.extend((
                _case(
                    "guarded_title_context",
                    embedded,
                    not any(normalize_title(item.get("title", "")) == normalized
                            for item in app.find_title_matches(embedded, app.MOVIES)),
                ),
                _case(
                    "guarded_title_context",
                    quoted,
                    any(normalize_title(item.get("title", "")) == normalized
                        for item in app.find_title_matches(quoted, app.MOVIES)),
                ),
                _case(
                    "guarded_title_context",
                    named,
                    any(normalize_title(item.get("title", "")) == normalized
                        for item in app.find_title_matches(named, app.MOVIES)),
                ),
            ))

    resolver = getattr(__import__("nlp.catalog_matching", fromlist=["resolve_like_title"]), "resolve_like_title", None)
    for source in app.MOVIES:
        query = f"movies like {source['title']}"
        resolved, _ = resolver(query, app.MOVIES) if resolver else (None, [])
        resolution_ok = bool(
            resolved and normalize_title(resolved.get("title", "")) == normalize_title(source["title"])
        )
        cases.append(_case("like_source", query, resolution_ok))

    if app.MOVIES:
        source = app.MOVIES[0]
        resolver = getattr(__import__("nlp.catalog_matching", fromlist=["resolve_like_title"]), "resolve_like_title", None)
        resolved, _ = resolver(f"movies like {source['title']}", app.MOVIES) if resolver else (None, [])
        model = getattr(app, "LIKE_TFIDF_MODEL", getattr(app, "TFIDF_MODEL", None))
        retrieve = getattr(app, "retrieve_candidates", None)
        recommendations = retrieve(
            f"movies like {source['title']}",
            {"similar_to": source["title"], "similar_to_movie_id": source["id"]},
            app.MOVIES,
            model,
        ) if resolved and retrieve and model else []
        cases.append(_case(
            "like_source_exclusion",
            f"movies like {source['title']}",
            bool(resolved and recommendations and all(
                normalize_title(item["movie"].get("title", ""))
                != normalize_title(resolved["title"])
                for item in recommendations
            )),
        ))
    unresolved, suggestions = resolver("movies like Interstllar", app.MOVIES) if resolver else (None, [])
    cases.append(_case(
        "like_source_suggestions",
        "movies like Interstllar",
        bool(not unresolved and suggestions and "Interstellar" in {
            movie.get("title") for movie in suggestions
        }),
    ))

    catalog_genres = _catalog_genres()
    for genre in catalog_genres:
        term = genre
        queries = [
            f"{cue} {term}"
            for cue in (
                "not", "no", "without", "anything but", "other than",
                "instead of", "rather than", "except", "skip", "avoid",
                "free of", "walang", "huwag", "wag", "ayaw", "ayoko",
                "hindi", "bawal",
            )
        ]
        queries.extend((
            f"-{term}",
            f"non-{term}",
            f"{term} no thanks",
            f"no {term} please",
            f"I'd rather not {term}",
            f"{term} is not what I want",
        ))
        if " " not in genre and "-" not in genre:
            queries.append(f"{genre}-free")
        for query in queries:
            preferences = extract_preferences(query)
            negated_ambiguity = any(
                item.get("negated") and item.get("tag") == genre
                for item in preferences.get("ambiguous_interpretations", [])
            )
            soft_exclusion = f"genre:{genre}" in preferences.get("soft_exclusions", [])
            cases.append(_case(
                "negation_scope",
                query,
                genre in preferences["excluded_genres"]
                or soft_exclusion
                or negated_ambiguity,
            ))
        for degree in query_rules["soft_degree_words"]:
            for cue in ("not", "nothing"):
                query = f"{cue} {degree} {term}"
                preferences = extract_preferences(query)
                cases.append(_case(
                    "soft_negation",
                    query,
                    f"genre:{genre}" in preferences.get("soft_exclusions", []),
                ))

    for genre in catalog_genres:
        for excluded in catalog_genres:
            if genre == excluded:
                continue
            queries = (
                f"{genre}, not {excluded}",
                f"not {excluded}, {genre}",
                f"{genre} but not {excluded}",
                f"{genre} without {excluded}",
                f"no {excluded}, {genre}",
                f"suspenseful {genre}, not {excluded}",
                f"{genre} and not {excluded}",
                f"{genre}, not {excluded}, under 90 minutes",
                f"{genre}, not {excluded}, from 2000 to 2010",
            )
            for query in queries:
                preferences = extract_preferences(query)
                cases.append(_case(
                    "genre_pair_scope",
                    query,
                    genre in preferences["genres"]
                    and excluded in preferences["excluded_genres"]
                    and genre not in preferences["excluded_genres"],
                ))

    for genre in catalog_genres:
        for excluded in catalog_genres:
            if excluded == genre:
                continue
            for second_excluded in catalog_genres:
                if second_excluded in {genre, excluded}:
                    continue
                queries = (
                    f"{genre}, not {excluded} or {second_excluded}",
                    f"{genre} without {excluded} or {second_excluded}",
                    f"{genre}, not {excluded}, not {second_excluded}",
                )
                for query in queries:
                    preferences = extract_preferences(query)
                    cases.append(_case(
                        "genre_triple_scope",
                        query,
                        genre in preferences["genres"]
                        and excluded in preferences["excluded_genres"]
                        and second_excluded in preferences["excluded_genres"],
                    ))

    unclear_negation = extract_preferences("avoid")
    cases.append(_case(
        "unclear_negation",
        "avoid",
        unclear_negation.get("unclear_negation", False),
    ))

    runtime_cases = {
        "1h30": (90, None),
        "1:30": (90, None),
        "90 min": (90, None),
        "ninety minutes": (90, None),
        "an hour and a half": (90, None),
        "two and a half hours": (150, None),
        "90-120": (90, 120),
        "between 90 and 120": (90, 120),
        "between 90 minutes and 2 hours": (90, 120),
    }
    for query, expected in runtime_cases.items():
        parsed = extract_runtime_constraint(query)
        cases.append(_case(
            "runtime_notation",
            query,
            (
                parsed["min"] == expected[0] and parsed["max"] == expected[1]
                if expected[1] is not None
                else parsed["target"] == expected[0]
                or parsed["min"] == expected[0]
            ),
        ))
    runtimes = sorted(
        movie.get("runtime", 0) for movie in app.MOVIES if movie.get("runtime")
    )
    for phrase, percentile, bound in (
        ("short", query_rules["runtime_percentiles"]["short"], "max"),
        ("quick", query_rules["runtime_percentiles"]["quick"], "max"),
        ("long", query_rules["runtime_percentiles"]["long"], "min"),
        ("not too long", query_rules["runtime_percentiles"]["not too long"], "max"),
    ):
        index = max(
            0,
            min(len(runtimes) - 1, round((len(runtimes) - 1) * percentile / 100)),
        )
        expected = runtimes[index]
        parsed = extract_preferences(phrase)["runtime"]
        cases.append(_case(
            "runtime_percentile",
            phrase,
            parsed[bound] == expected,
        ))

    country_labels = getattr(app, "COUNTRY_LABELS", None)
    if country_labels is None:
        from nlp.query import COUNTRY_LABELS as country_labels
    movie_country_codes = getattr(
        app, "movie_country_codes",
        lambda movie: movie.get("origin_regions", []),
    )
    country_codes = {
        code
        for movie in app.MOVIES
        for code in movie_country_codes(movie) or []
        if code in country_labels
    }
    for code in sorted(country_codes):
        country_name = country_labels[code]
        detected, _ = detect_country(normalize_text(f"movies from {country_name}"))
        cases.append(_case("country", f"movies from {country_name}", detected == code))

    for intent, mapping in query_rules["audience_occasions"].items():
        for phrase in mapping["phrases"]:
            parsed = build_structured_query(phrase)["preferences"]
            cases.append(_case(
                "audience_occasion",
                phrase,
                intent in parsed.get("audience_intents", []),
            ))

    for query, expected in (
        ("best", "best"),
        ("top", "best"),
        ("highest rated", "best"),
        ("greatest", "best"),
        ("worst", "worst"),
        ("lowest rated", "worst"),
    ):
        parsed = build_structured_query(query)
        cases.append(_case(
            "superlative",
            query,
            parsed["ranking_intent"] == expected,
        ))
    cases.extend(_genre_exclusion_filter_cases())
    return cases


def report_rates(cases):
    report = {}
    for category in sorted({item["category"] for item in cases}):
        category_cases = [item for item in cases if item["category"] == category]
        report[category] = {}
        for split in ("tuning", "held_out"):
            selected = [item for item in category_cases if item["split"] == split]
            passed = sum(item["passed"] for item in selected)
            report[category][split] = {
                "passed": passed,
                "total": len(selected),
                "rate": round(passed / len(selected), 4) if selected else None,
            }
    return report


if __name__ == "__main__":
    generated = generate_cases()
    result = {
        "total": len(generated),
        "passed": sum(item["passed"] for item in generated),
        "rates": report_rates(generated),
        "failures": [
            {"category": item["category"], "query": item["query"]}
            for item in generated if not item["passed"]
        ],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["passed"] == result["total"] else 1)
