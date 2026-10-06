"""Resumable TMDB cast/credit enrichment for movie gender verification.

This script is intentionally conservative:
- it resolves a TMDB id by IMDb id when possible;
- it falls back to title+year+runtime only when the match is clear;
- it logs ambiguous or unmatched titles to a CSV for manual review;
- it stores raw TMDB payloads under data/tmdb_cache/ for zero-cost reruns.

Run:
    $env:TMDB_API_KEY = "your_key"
    python data/enrich_cast.py
    # or: npm run enrich-cast
"""

from __future__ import annotations

import csv
import ast
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
CATALOG_CANDIDATES = [DATA_DIR / "imdb_movies.json", DATA_DIR / "popular_movies.json", DATA_DIR / "generated_movies.py"]
TARGET_PATH = DATA_DIR / "movie_cast_enrichment.json"
PROGRESS_PATH = DATA_DIR / "movie_cast_progress.json"
REVIEW_CSV = DATA_DIR / "tmdb_cast_review.csv"
REPORT_PATH = DATA_DIR / "cast_coverage_report.json"
CACHE_DIR = DATA_DIR / "tmdb_cache"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_TOP_CAST_SIZE = 15


def ensure_dirs():
    CACHE_DIR.mkdir(exist_ok=True)


def cache_key_for_url(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def read_json_cache(url):
    path = CACHE_DIR / f"{cache_key_for_url(url)}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def write_json_cache(url, payload):
    path = CACHE_DIR / f"{cache_key_for_url(url)}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def request_json(url, api_key, retries=3):
    cached = read_json_cache(url)
    if cached is not None:
        return cached
    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
                write_json_cache(url, payload)
                return payload
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if hasattr(exc, "code") and exc.code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            if attempt < retries - 1:
                time.sleep(1.5 ** (attempt + 1))
                continue
    raise last_error or RuntimeError(f"TMDB request failed for {url}")


def load_catalog():
    configured_path = os.environ.get("TMDB_CATALOG_PATH", "").strip()
    if configured_path.lower() in {"deployment", "render", "compact"}:
        source = ast.parse((DATA_DIR / "movies.py").read_text(encoding="utf-8"))
        assignment = next(
            node for node in source.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "MOVIES" for target in node.targets)
        )
        curated = ast.literal_eval(assignment.value)
        popular = json.loads((DATA_DIR / "popular_movies.json").read_text(encoding="utf-8"))
        return [*curated, *popular]
    if configured_path:
        candidate = Path(configured_path)
        if not candidate.is_absolute():
            candidate = DATA_DIR.parent / candidate
        candidates = [candidate]
    else:
        candidates = CATALOG_CANDIDATES
    for candidate in candidates:
        if candidate.exists():
            if candidate.name.endswith(".json"):
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    return data
            if candidate.name == "generated_movies.py":
                import importlib.util
                spec = importlib.util.spec_from_file_location("generated_movies", candidate)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.GENERATED_MOVIES
    raise FileNotFoundError("No movie catalog found. Set TMDB_CATALOG_PATH or add data/imdb_movies.json / data/popular_movies.json.")


def title_similarity(left, right):
    left_tokens = set(re.sub(r"[^a-z0-9]+", " ", left.lower()).split())
    right_tokens = set(re.sub(r"[^a-z0-9]+", " ", right.lower()).split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, len(left_tokens | right_tokens))


def normalized_title(title):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split())


def catalog_record_key(movie):
    if movie.get("imdb_id"):
        return str(movie["imdb_id"])
    if movie.get("id") is not None:
        return f"id:{movie['id']}"
    return f"{movie.get('title')}:{movie.get('release_year')}"


def write_json_atomic(path, payload):
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def match_tmdb_movie(movie, api_key):
    imdb_id = (movie.get("imdb_id") or "").strip()
    if imdb_id:
        url = f"{TMDB_BASE_URL}/find/{urllib.parse.quote(imdb_id)}?api_key={urllib.parse.quote(api_key)}&external_source=imdb_id"
        payload = request_json(url, api_key)
        movie_results = payload.get("movie_results") or []
        if movie_results:
            return movie_results[0], "imdb_id"
    title = (movie.get("title") or "").strip()
    year = movie.get("release_year")
    if title:
        url = f"{TMDB_BASE_URL}/search/movie?api_key={urllib.parse.quote(api_key)}&query={urllib.parse.quote(title)}&include_adult=false"
        if year:
            url += f"&year={year}"
        payload = request_json(url, api_key)
        results = payload.get("results") or []
        candidates = []
        normalized_source_title = normalized_title(title)
        for result in results:
            result_title = normalized_title(result.get("title"))
            original_title = normalized_title(result.get("original_title"))
            result_year = (result.get("release_date") or "")[:4]
            if normalized_source_title not in {result_title, original_title}:
                continue
            if year and (not result_year or int(result_year) != int(year)):
                continue
            candidates.append(result)

        runtime = movie.get("runtime") or 0
        verified = []
        for candidate in candidates:
            if runtime:
                details, _ = fetch_movie_details(candidate.get("id"), api_key)
                tmdb_runtime = details.get("runtime")
                if not tmdb_runtime or abs(int(tmdb_runtime) - int(runtime)) > 5:
                    continue
            verified.append(candidate)
        if len(verified) == 1:
            return verified[0], "title_year_runtime" if runtime else "title_year"
        if len(verified) > 1 or (candidates and not runtime):
            return None, "ambiguous"
        return None, "unmatched"
    return None, "unmatched"


def fetch_movie_details(movie_id, api_key):
    movie_url = f"{TMDB_BASE_URL}/movie/{movie_id}?api_key={urllib.parse.quote(api_key)}&append_to_response=keywords,release_dates"
    details = request_json(movie_url, api_key)
    credits_url = f"{TMDB_BASE_URL}/movie/{movie_id}/credits?api_key={urllib.parse.quote(api_key)}"
    credits = request_json(credits_url, api_key)
    return details, credits


def normalize_cast_entry(raw):
    if isinstance(raw, str):
        return {"name": raw, "character": "", "billing_order": 0, "gender": 0}
    if not isinstance(raw, dict):
        return {"name": "", "character": "", "billing_order": 0, "gender": 0}
    gender = raw.get("gender")
    if isinstance(gender, str):
        key = gender.strip().lower()
        gender = {"female": 1, "woman": 1, "male": 2, "man": 2, "nonbinary": 3, "non-binary": 3, "nb": 3}.get(key, 0)
    elif gender not in {0, 1, 2, 3}:
        gender = 0
    return {
        "name": (raw.get("name") or "").strip(),
        "character": (raw.get("character") or raw.get("character_name") or "").strip(),
        "billing_order": int(raw.get("order") or 0),
        "gender": int(gender),
    }


def compute_cast_fields(cast_entries, top_n=DEFAULT_TOP_CAST_SIZE):
    top_cast = [normalize_cast_entry(entry) for entry in cast_entries[:top_n] if isinstance(entry, dict) or isinstance(entry, str)]
    female_count = sum(1 for person in top_cast if person.get("gender") == 1)
    male_count = sum(1 for person in top_cast if person.get("gender") == 2)
    nonbinary_count = sum(1 for person in top_cast if person.get("gender") == 3)
    unspecified_count = sum(1 for person in top_cast if person.get("gender") == 0)
    known_gender_count = female_count + male_count + nonbinary_count
    coverage = (known_gender_count / len(top_cast)) if top_cast else 0.0
    return {
        "cast": top_cast,
        "top_cast_size": len(top_cast),
        "female_count": female_count,
        "male_count": male_count,
        "nonbinary_count": nonbinary_count,
        "unspecified_count": unspecified_count,
        "gender_coverage": round(coverage, 3),
        "known_gender_count": known_gender_count,
    }


def enrich_catalog():
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        print("TMDB cast enrichment skipped: TMDB_API_KEY is not configured; catalog unchanged.")
        return 0
    ensure_dirs()
    catalog = load_catalog()
    progress_data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8")) if PROGRESS_PATH.exists() else {}
    if "_status" in progress_data:
        statuses = progress_data.get("_status", {})
        review_by_key = progress_data.get("_review", {})
    else:
        statuses = progress_data
        review_by_key = {}
    existing_results = json.loads(TARGET_PATH.read_text(encoding="utf-8")) if TARGET_PATH.exists() else []
    results_by_key = {catalog_record_key(movie): movie for movie in existing_results}
    processed_count = 0
    for index, movie in enumerate(catalog):
        movie_key = catalog_record_key(movie)
        if statuses.get(movie_key) == "done" and movie_key in results_by_key:
            continue
        if statuses.get(movie_key) in {"ambiguous", "unmatched"}:
            continue
        try:
            tmdb_movie, match_mode = match_tmdb_movie(movie, api_key)
        except Exception as exc:
            statuses[movie_key] = "failed"
            review_by_key[movie_key] = {
                "movie_id": movie_key,
                "title": movie.get("title"),
                "reason": f"request_failed: {type(exc).__name__}",
                "year": movie.get("release_year"),
            }
            write_json_atomic(PROGRESS_PATH, {"_status": statuses, "_review": review_by_key})
            continue
        if not tmdb_movie:
            statuses[movie_key] = match_mode
            review_by_key[movie_key] = {
                "movie_id": movie_key,
                "title": movie.get("title"),
                "reason": match_mode,
                "year": movie.get("release_year"),
            }
            write_json_atomic(PROGRESS_PATH, {"_status": statuses, "_review": review_by_key})
            continue
        try:
            details, credits = fetch_movie_details(tmdb_movie.get("id"), api_key)
        except Exception as exc:
            statuses[movie_key] = "failed"
            review_by_key[movie_key] = {
                "movie_id": movie_key,
                "title": movie.get("title"),
                "reason": f"request_failed: {type(exc).__name__}",
                "year": movie.get("release_year"),
            }
            write_json_atomic(PROGRESS_PATH, {"_status": statuses, "_review": review_by_key})
            continue
        credits_cast = credits.get("cast") or []
        cast_fields = compute_cast_fields(credits_cast, DEFAULT_TOP_CAST_SIZE)
        movie["tmdb_id"] = tmdb_movie.get("id")
        movie["cast"] = cast_fields["cast"]
        tmdb_overview = (details.get("overview") or "").strip()
        movie["overview"] = tmdb_overview or (movie.get("overview") or movie.get("synopsis") or "").strip()
        if tmdb_overview:
            movie["synopsis"] = tmdb_overview
        movie["tagline"] = (details.get("tagline") or "").strip()
        tmdb_runtime = details.get("runtime")
        if isinstance(tmdb_runtime, int) and tmdb_runtime > 0:
            movie["runtime"] = tmdb_runtime
        movie["original_language"] = (details.get("original_language") or "").strip().lower()
        poster_path = details.get("poster_path")
        if isinstance(poster_path, str) and poster_path.startswith("/"):
            movie["poster_path"] = poster_path
        production_countries = [
            country.get("iso_3166_1")
            for country in details.get("production_countries", [])
            if isinstance(country, dict) and country.get("iso_3166_1")
        ]
        if production_countries:
            movie["production_countries"] = list(dict.fromkeys(production_countries))
            movie["country_source"] = "TMDB production country"
        movie["certification"] = ""
        release_dates = details.get("release_dates") or {}
        if isinstance(release_dates, dict):
            for country in release_dates.get("results") or []:
                for item in country.get("release_dates") or []:
                    cert = item.get("certification")
                    if cert:
                        movie["certification"] = cert
                        break
                if movie.get("certification"):
                    break
        tmdb_keywords = [
            entry.get("name", "")
            for entry in (details.get("keywords") or {}).get("keywords", [])
            if entry.get("name")
        ]
        existing_keywords = movie.get("keywords") or []
        movie["keywords"] = list(dict.fromkeys([*existing_keywords, *tmdb_keywords]))
        movie["female_character_mentions"] = []
        overview_text = (movie.get("overview") or movie.get("synopsis") or "") + " " + " ".join(item.get("character", "") for item in cast_fields["cast"])
        overview_tokens = set(re.sub(r"[^a-z0-9]+", " ", overview_text.lower()).split())
        for token in ["mother", "queen", "princess", "girl", "woman", "women", "daughter", "wife", "widow", "sister", "female", "she", "her"]:
            if token in overview_tokens:
                movie["female_character_mentions"].append(token)
        if cast_fields["female_count"] > 0 or movie["female_character_mentions"]:
            movie["all_male_evidence"] = "contradicted"
        elif cast_fields["top_cast_size"] >= 5 and cast_fields["gender_coverage"] >= 0.9:
            movie["all_male_evidence"] = "strong"
        elif cast_fields["top_cast_size"] >= 5 and cast_fields["gender_coverage"] >= 0.7:
            movie["all_male_evidence"] = "moderate"
        elif cast_fields["top_cast_size"] < 5:
            movie["all_male_evidence"] = "weak"
        else:
            movie["all_male_evidence"] = "unknown"
        movie.update({
            "top_cast_size": cast_fields["top_cast_size"],
            "female_count": cast_fields["female_count"],
            "male_count": cast_fields["male_count"],
            "nonbinary_count": cast_fields["nonbinary_count"],
            "unspecified_count": cast_fields["unspecified_count"],
            "gender_coverage": cast_fields["gender_coverage"],
            "gender_known_count": cast_fields["known_gender_count"],
            "cast_enrichment_source": "TMDB",
            "cast_match_method": match_mode,
        })
        results_by_key[movie_key] = movie
        statuses[movie_key] = "done"
        review_by_key.pop(movie_key, None)
        write_json_atomic(TARGET_PATH, list(results_by_key.values()))
        write_json_atomic(PROGRESS_PATH, {"_status": statuses, "_review": review_by_key})
        processed_count += 1
        if processed_count % 10 == 0:
            print(f"Processed {index + 1}/{len(catalog)}")
        time.sleep(0.35)

    review_rows = list(review_by_key.values())
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["movie_id", "title", "reason", "year"])
        writer.writeheader()
        writer.writerows(review_rows)

    status_order = {"strong": 0, "moderate": 1, "weak": 2, "unknown": 3, "contradicted": 4}
    enriched = list(results_by_key.values())
    coverage_counts = {
        status: sum(1 for movie in enriched if movie.get("all_male_evidence") == status)
        for status in status_order
    }
    top_five = sorted(
        enriched,
        key=lambda movie: (
            status_order.get(movie.get("all_male_evidence"), 3),
            -(movie.get("gender_coverage") or 0),
            -(movie.get("vote_count") or 0),
        ),
    )[:5]
    report = {
        "catalog_records": len(catalog),
        "enriched_records": len(enriched),
        "unmatched_or_ambiguous_records": len(review_rows),
        "gender_coverage_counts": coverage_counts,
        "gender_known_records": sum(1 for movie in enriched if (movie.get("gender_known_count") or 0) > 0),
        "top_five_evidence": [
            {
                "title": movie.get("title"),
                "release_year": movie.get("release_year"),
                "status": movie.get("all_male_evidence"),
                "gender_coverage": movie.get("gender_coverage"),
                "evidence": (
                    f"Top {movie.get('top_cast_size', 0)} billed cast: "
                    f"{movie.get('male_count', 0)} male, {movie.get('female_count', 0)} female, "
                    f"{movie.get('nonbinary_count', 0)} non-binary, "
                    f"{movie.get('unspecified_count', 0)} unspecified."
                ),
            }
            for movie in top_five
        ],
    }
    write_json_atomic(REPORT_PATH, report)
    print(
        f"Catalog {len(catalog):,}; enriched {len(enriched):,}; "
        f"unmatched/ambiguous {len(review_rows):,}. Coverage report: {REPORT_PATH.name}."
    )


if __name__ == "__main__":
    enrich_catalog()
