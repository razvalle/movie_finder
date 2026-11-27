"""Pre-deployment check for query-history-dependent search results."""

import hashlib
import json
import pickle
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module
from data.movies import MOVIES


def _fingerprint(value):
    return hashlib.sha256(pickle.dumps(value, protocol=5)).hexdigest()


def _shared_fingerprints():
    return {
        name: _fingerprint(value)
        for name, value in {
            "catalog": MOVIES,
            "tfidf": app_module.TFIDF_MODEL,
            "like_tfidf": app_module.LIKE_TFIDF_MODEL,
            "concepts": app_module.CONCEPT_TABLE,
            "genre_postings": app_module.GENRE_MOVIE_IDS,
            "title_index": app_module.TITLE_INDEX,
            "exact_title_index": app_module.TITLE_EXACT_INDEX,
            "title_token_index": app_module.TITLE_TOKEN_INDEX,
        }.items()
    }


def _query_cases():
    genres = sorted({
        app_module._canonical_genre(genre)
        for movie in MOVIES
        for genre in movie.get("genres", [])
        if isinstance(genre, str) and genre.strip()
    })
    pairs = [
        (genre, excluded)
        for genre in genres
        for excluded in genres
        if genre != excluded and any(
            genre in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            }
            and excluded not in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            }
            for movie in MOVIES
        )
    ]
    positive, excluded = pairs[len(pairs) // 2]
    anchor = next(
        movie for movie in MOVIES
        if positive in {
            app_module._canonical_genre(value)
            for value in movie.get("genres", [])
        }
        and excluded not in {
            app_module._canonical_genre(value)
            for value in movie.get("genres", [])
        }
        and movie.get("runtime")
        and movie.get("release_year")
    )
    moods = sorted({
        str(mood).strip().casefold()
        for movie in MOVIES
        for mood in movie.get("mood_tags", [])
        if isinstance(mood, str) and mood.strip()
    })
    country_codes = sorted(app_module.movie_country_codes(anchor))
    cases = [
        {
            "name": "catalog-genre",
            "params": {"q": f"{positive} movies", "per_page": "48"},
            "oracle": lambda movie: positive in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            },
        },
        {
            "name": "catalog-exclusion",
            "params": {"q": f"movies not {excluded}", "per_page": "48"},
            "oracle": lambda movie: excluded not in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            },
        },
        {
            "name": "catalog-genre-exclusion-pair",
            "params": {"q": f"{positive}, not {excluded}", "per_page": "48"},
            "oracle": lambda movie: positive in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            } and excluded not in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            },
        },
        {
            "name": "catalog-runtime-year",
            "params": {
                "q": (
                    f"{positive} movies under {int(anchor['runtime']) + 1} minutes "
                    f"after {int(anchor['release_year']) - 1}"
                ),
                "per_page": "48",
            },
            "oracle": lambda movie: (
                positive in {
                    app_module._canonical_genre(value)
                    for value in movie.get("genres", [])
                }
                and movie.get("runtime") is not None
                and movie["runtime"] < int(anchor["runtime"]) + 1
                and movie.get("release_year") is not None
                and movie["release_year"] > int(anchor["release_year"]) - 1
            ),
        },
        {
            "name": "catalog-title",
            "params": {"q": anchor["title"], "per_page": "48"},
            "oracle": lambda movie: movie.get("id") == anchor.get("id"),
        },
        {
            "name": "catalog-like-title",
            "params": {"q": f"movies like {anchor['title']}", "per_page": "48"},
            "oracle": lambda movie: movie.get("id") != anchor.get("id"),
        },
        {
            "name": "catalog-mood",
            "params": {"q": f"{moods[len(moods) // 2]} movies", "per_page": "48"},
            "oracle": None,
        },
        {
            "name": "catalog-chip-plus-exclusion",
            "params": {
                "q": f"not {excluded}",
                "genre": positive,
                "per_page": "48",
            },
            "oracle": lambda movie: positive in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            } and excluded not in {
                app_module._canonical_genre(value)
                for value in movie.get("genres", [])
            },
        },
        {
            "name": "catalog-country",
            "params": {
                "q": f"movies from {app_module.COUNTRY_LABELS[country_codes[0]]}",
                "per_page": "48",
            },
            "oracle": lambda movie: country_codes[0] in app_module.movie_country_codes(movie),
        },
        {
            "name": "catalog-recognition-filter",
            "params": {"q": "", "awards": "winners", "per_page": "24"},
            "oracle": None,
        },
    ]
    return cases


def _search_signature(client, case):
    response = client.get("/", query_string=case["params"])
    if response.status_code != 200:
        raise AssertionError(f"{case['name']} returned HTTP {response.status_code}")
    context = app_module.g.search_context
    results = tuple(
        (
            str(item["movie"].get("id", "")),
            item.get("percent"),
            item.get("match_tier"),
            item.get("match_badge"),
            tuple(reason.get("text", "") for reason in item.get("reasons", [])),
        )
        for item in context.get("results", [])
    )
    return (
        context.get("result_count"),
        context.get("result_count_label"),
        tuple(sorted(context.get("result_tier_counts", {}).items())),
        context.get("page_number"),
        context.get("total_pages"),
        results,
    )


def run_sweep():
    cases = _query_cases()
    before = _shared_fingerprints()
    failures = []
    comparisons = 0
    oracle_checks = 0
    with (
        app_module.app.test_client() as client,
        patch("app.allow_search", return_value=True),
        patch("app.write_search_record"),
    ):
        baseline = []
        for case in cases:
            signature = _search_signature(client, case)
            baseline.append(signature)
            oracle = case["oracle"]
            if oracle:
                oracle_checks += 1
                if any(oracle(movie) for movie in MOVIES) and not signature[0]:
                    failures.append(f"{case['name']}: catalog oracle found matches, search returned zero")

        for index, case in enumerate(cases):
            for offset in (1, 2):
                history = cases[(index + offset) % len(cases)]
                _search_signature(client, history)
            actual = _search_signature(client, case)
            comparisons += 1
            if actual != baseline[index]:
                failures.append(f"{case['name']}: result signature changed after query history")

    after = _shared_fingerprints()
    changed = sorted(name for name in before if before[name] != after[name])
    if changed:
        failures.append("shared catalog/index data mutated: " + ", ".join(changed))

    return {
        "queries": len(cases),
        "history_comparisons": comparisons,
        "history_passed": comparisons - sum(
            "result signature changed" in failure for failure in failures
        ),
        "oracle_checks": oracle_checks,
        "shared_structures_unchanged": not changed,
        "failures": failures,
    }


def main():
    result = run_sweep()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
