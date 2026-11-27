"""Pre-deployment sweep for catalog-derived negated-genre behavior."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.extract import extract_preferences
from tests.query_understanding_sweep import (
    _case,
    _catalog_genres,
    _genre_exclusion_filter_cases,
    report_rates,
)


def generate_cases():
    genres = _catalog_genres()
    cases = []
    cues = (
        "not", "no", "without", "except", "anything but", "other than",
        "instead of", "rather than", "skip", "avoid", "free of", "non-",
        "walang", "huwag", "wag", "ayaw", "ayoko", "hindi", "bawal",
    )
    for genre in genres:
        queries = [f"{cue}{genre}" if cue == "non-" else f"{cue} {genre}" for cue in cues]
        queries.extend((
            f"-{genre}",
            f"{genre}-free",
            f"{genre} no thanks",
            f"no {genre} please",
            f"{genre} is not what I want",
            f"I'd rather not {genre}",
        ))
        for query in queries:
            preferences = extract_preferences(query)
            cases.append(_case(
                "single_genre_negation",
                query,
                genre in preferences["excluded_genres"],
            ))

    for genre in genres:
        for excluded in genres:
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
                    "ordered_genre_pair",
                    query,
                    genre in preferences["genres"]
                    and excluded in preferences["excluded_genres"]
                    and genre not in preferences["excluded_genres"],
                ))

    for genre in genres:
        for excluded in genres:
            if excluded == genre:
                continue
            for second_excluded in genres:
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
                        "ordered_genre_triple",
                        query,
                        genre in preferences["genres"]
                        and excluded in preferences["excluded_genres"]
                        and second_excluded in preferences["excluded_genres"],
                    ))

    for genre in genres:
        if not genre.isalpha() or len(genre) < 5:
            continue
        typo = genre[:1] + genre[2:]
        preferences = extract_preferences(f"not {typo}")
        cases.append(_case(
            "catalog_derived_typo",
            f"not {typo}",
            preferences.get("fuzzy_corrections", {}).get(typo) == genre
            and genre in preferences["excluded_genres"],
        ))

    cases.extend(_genre_exclusion_filter_cases())
    return cases


def main():
    cases = generate_cases()
    result = {
        "total": len(cases),
        "passed": sum(case["passed"] for case in cases),
        "rates": report_rates(cases),
        "failures": [
            {"category": case["category"], "query": case["query"]}
            for case in cases if not case["passed"]
        ],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
