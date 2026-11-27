"""Catalog-generated human-query checks with deterministic tuning/held-out splits."""

import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.movies import MOVIES
from data.synonyms import GENRE_SYNONYMS, MOOD_SYNONYMS
from nlp.catalog_matching import normalize_title, resolve_like_title
from nlp.human_input import load_human_input_rules, normalize_human_input
from nlp.smart_search import interpret_query


def _intent_corpus():
    genres = sorted({
        genre for movie in MOVIES for genre in movie.get("genres", [])
        if genre in GENRE_SYNONYMS
    })
    catalog_moods = {
        mood for movie in MOVIES for mood in movie.get("mood_tags", [])
    }
    moods = sorted(
        canonical for canonical, aliases in MOOD_SYNONYMS.items()
        if canonical in catalog_moods or any(alias in catalog_moods for alias in aliases)
    )
    runtimes = sorted(
        int(movie["runtime"]) for movie in MOVIES
        if isinstance(movie.get("runtime"), int) and movie["runtime"] > 0
    )
    years = sorted(
        int(movie["release_year"]) for movie in MOVIES
        if isinstance(movie.get("release_year"), int) and movie["release_year"] > 1880
    )
    titles = sorted({
        movie["title"] for movie in MOVIES if movie.get("title")
    }, key=str.casefold)
    return genres, moods, runtimes, years, titles


def _noise_query(query, target, operations):
    value = query
    for operation in operations:
        if operation == "smart-punctuation":
            value = value.replace("'", "’").replace('"', "“")
        elif operation == "elongation" and target:
            first = target.split()[0]
            vowel = next((char for char in first if char.casefold() in "aeiou"), None)
            if vowel:
                noisy = vowel + vowel * 3
                value = value.replace(first, first.replace(vowel, noisy, 1), 1)
        elif operation == "hashtag" and target:
            camel = "".join(part.title() for part in target.split())
            value = value.replace(target, f"#{camel}", 1)
        elif operation == "nonbreaking-space":
            value = value.replace(" ", "\u00a0")
        elif operation == "zero-width" and target:
            first = target.split()[0]
            if len(first) > 2:
                point = len(first) // 2
                value = value.replace(first, first[:point] + "\u200b" + first[point:], 1)
        elif operation == "joined-words" and target:
            phrase = " ".join(target.split())
            if " " in phrase:
                value = value.replace(phrase, phrase.replace(" ", ""), 1)
            else:
                value = value.replace(phrase + " ", phrase, 1)
        elif operation == "emoji":
            value = "😂 " + value
    return value


def _trials():
    rules = load_human_input_rules()
    personas = rules["personas"]
    behavior_templates = rules["behavior_templates"]
    noise_levels = rules["noise_levels"]
    genres, moods, runtimes, years, titles = _intent_corpus()
    trials = []

    def add(persona, behavior, noise_level, query, target, expected):
        key = f"{persona}\0{behavior}\0{noise_level}\0{query}"
        split = "held-out" if int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 5 == 0 else "tuning"
        trials.append({
            "persona": persona,
            "behavior": behavior,
            "noise_level": noise_level,
            "query": query,
            "target": target,
            "expected": expected,
            "split": split,
        })

    noise_names = list(noise_levels)
    persona_names = list(personas)
    for index, (persona, persona_templates) in enumerate(personas.items()):
        genre = genres[index % len(genres)] if genres else "movie"
        mood = moods[index % len(moods)] if moods else "mood"
        runtime = runtimes[index % len(runtimes)] if runtimes else 90
        year = years[index % len(years)] if years else 2000
        cases = [
            (
                "genre",
                behavior_templates["genre"][0].format(genre=genre),
                genre,
                {"genres": [genre]},
            ),
            (
                "mood",
                behavior_templates["mood"][0].format(mood=mood),
                mood,
                {"moods": [mood]},
            ),
            (
                "negation",
                behavior_templates["negation"][0].format(genre=genre),
                genre,
                {"excluded_genres": [genre]},
            ),
            (
                "runtime-year",
                behavior_templates["runtime-year"][0].format(
                    genre=genre, runtime=runtime + 1, year=year - 1,
                ),
                genre,
                {"runtime_max": runtime, "year_min": year},
            ),
        ]
        template = persona_templates[index % len(persona_templates)]
        for behavior, intent, target, expected in cases:
            for noise_level in noise_names:
                query = template.format(intent=intent)
                query = _noise_query(query, target, noise_levels[noise_level])
                add(persona, behavior, noise_level, query, target, expected)

    # Exercise every catalog genre and supported mood synonym under each noise
    # level as a separate catalog-derived layer, beyond the persona sample.
    for behavior, values, template_key, expected_key in (
        ("genre", genres, "genre", "genres"),
        ("mood", moods, "mood", "moods"),
        ("negation", genres, "negation", "excluded_genres"),
    ):
        for index, value in enumerate(values):
            persona = persona_names[index % len(persona_names)]
            intent = behavior_templates[template_key][0].format(
                **{"genre" if behavior != "mood" else "mood": value}
            )
            for noise_level in noise_names:
                query = _noise_query(intent, value, noise_levels[noise_level])
                add(
                    persona,
                    behavior,
                    noise_level,
                    query,
                    value,
                    {expected_key: [value]},
                )

    # Source-title parsing is checked for the complete catalog, independently
    # of persona phrasing; the route-level harness covers ranked results.
    for title in titles:
        query = behavior_templates["similarity"][0].format(title=title)
        add("catalog-title", "similarity", "none", query, title, {"similar_to": title})
    return trials


def _passes(actual, expected):
    if "genres" in expected:
        return expected["genres"][0] in actual.get("genres", [])
    if "moods" in expected:
        target = expected["moods"][0]
        return (
            target in actual.get("mood_tone", [])
            or any(
                item.get("category") == "mood"
                and item.get("tag") == target
                and not item.get("negated")
                for item in actual.get("ambiguous_interpretations", [])
            )
        )
    if "excluded_genres" in expected:
        return expected["excluded_genres"][0] in actual.get("filters", {}).get("excluded_genres", [])
    if "runtime_max" in expected:
        runtime = actual.get("filters", {}).get("runtime", {})
        year = actual.get("year_range", {})
        return (
            runtime.get("max") == expected["runtime_max"]
            and year.get("from") == expected["year_min"]
        )
    if "similar_to" in expected:
        actual_title = actual.get("similar_to") or ""
        expected_title = expected["similar_to"]
        if normalize_title(actual_title) != normalize_title(expected_title):
            return False
        article = lambda title: title.casefold().split(maxsplit=1)[0] if title.casefold().startswith(("the ", "a ", "an ")) else ""
        return article(actual_title) == article(expected_title)
    return False


def _percentile(samples, percentile):
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)]


def run_benchmark():
    groups = defaultdict(lambda: {"passed": 0, "total": 0, "latencies_ms": []})
    started = time.perf_counter()
    trials = _trials()
    for trial in trials:
        before = time.perf_counter()
        if "similar_to" in trial["expected"]:
            normalized = normalize_human_input(trial["query"], MOVIES)["text"]
            resolved, _ = resolve_like_title(normalized, MOVIES)
            actual = {
                "similar_to": resolved.get("title") if resolved else None,
            }
        else:
            actual = interpret_query(trial["query"])
        elapsed_ms = (time.perf_counter() - before) * 1000
        passed = _passes(actual, trial["expected"])
        keys = (
            ("overall",),
            ("split", trial["split"]),
            ("persona", trial["persona"]),
            ("behavior", trial["behavior"]),
            ("noise_level", trial["noise_level"]),
        )
        for key in keys:
            group = groups[key]
            group["total"] += 1
            group["passed"] += int(passed)
            group["latencies_ms"].append(elapsed_ms)
    elapsed_seconds = time.perf_counter() - started
    summary = {}
    for key, group in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        summary["/".join(key)] = {
            "passed": group["passed"],
            "total": group["total"],
            "pass_rate": round(group["passed"] / group["total"], 4) if group["total"] else 0,
            "p95_ms": round(_percentile(group["latencies_ms"], 0.95), 2),
            "p99_ms": round(_percentile(group["latencies_ms"], 0.99), 2),
        }
    return {
        "catalog_movies": len(MOVIES),
        "trials": len(trials),
        "runtime_seconds": round(elapsed_seconds, 2),
        "metrics": summary,
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), ensure_ascii=False, indent=2))
