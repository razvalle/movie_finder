"""Enrich the compact movie catalog from public Wikidata/Wikimedia data.

No API key is required. Only movie-specific Commons images whose filenames
identify them as posters are used; other image types are deliberately skipped.
Run from the project root: python data/enrich_public_metadata.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
DATA_DIR = ROOT_DIR / "data"
OUTPUT_PATH = DATA_DIR / "movie_public_enrichment.json"
PROGRESS_PATH = DATA_DIR / "movie_public_progress.json"
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
BATCH_SIZE = 50
REQUEST_DELAY_SECONDS = 1.0
USER_AGENT = "MovieFinderGroup4/1.0 (public movie metadata enrichment)"


def build_wikidata_query(imdb_ids):
    values = " ".join(json.dumps(str(imdb_id)) for imdb_id in imdb_ids)
    return f"""
SELECT ?imdbID ?countryCode ?image WHERE {{
  VALUES ?imdbID {{ {values} }}
  ?item wdt:P345 ?imdbID .
  OPTIONAL {{
    ?item wdt:P495 ?country .
    OPTIONAL {{ ?country wdt:P297 ?countryCode . }}
  }}
  OPTIONAL {{ ?item wdt:P18 ?image . }}
}}
""".strip()


def build_wikidata_title_query(movies):
        values = " ".join(
                f"({json.dumps(str(movie.get('title') or ''))} {int(movie.get('release_year') or 0)})"
                for movie in movies
        )
        return f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?requestedTitle ?requestedYear ?countryCode ?image WHERE {{
    VALUES (?searchTitle ?requestedYear) {{ {values} }}
    ?item wdt:P31 wd:Q11424 ; rdfs:label ?englishLabel ; wdt:P577 ?releaseDate .
    FILTER(LANG(?englishLabel) = "en" && STR(?englishLabel) = ?searchTitle && YEAR(?releaseDate) = ?requestedYear)
    BIND(?searchTitle AS ?requestedTitle)
    OPTIONAL {{ ?item wdt:P495 ?country . OPTIONAL {{ ?country wdt:P297 ?countryCode . }} }}
    OPTIONAL {{ ?item wdt:P18 ?image . }}
}}
""".strip()


def fetch_metadata(imdb_ids):
    query = build_wikidata_query(imdb_ids)
    url = WIKIDATA_ENDPOINT + "?query=" + urllib.parse.quote(query, safe="") + "&format=json"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("results", {}).get("bindings", [])


def fetch_title_metadata(movies):
    query = build_wikidata_title_query(movies)
    url = WIKIDATA_ENDPOINT + "?query=" + urllib.parse.quote(query, safe="") + "&format=json"
    for attempt in range(3):
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("results", {}).get("bindings", [])
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return []


def poster_fields(image_url):
    if not isinstance(image_url, str):
        return {}
    parsed = urllib.parse.urlsplit(image_url)
    if parsed.hostname not in {"commons.wikimedia.org", "upload.wikimedia.org"}:
        return {}
    filename = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    if "poster" not in filename.casefold():
        return {}
    normalized_url = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    filename_for_link = filename
    if "/Special:FilePath/" in parsed.path:
        filename_for_link = urllib.parse.unquote(parsed.path.split("/Special:FilePath/", 1)[1])
    source_url = "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(filename_for_link.replace(" ", "_"), safe="()!,._-")
    return {
        "poster_url": normalized_url,
        "poster_source_url": source_url,
        "poster_source": "Wikimedia Commons via Wikidata",
    }


def records_from_bindings(movies, bindings):
    by_imdb_id = {}
    for row in bindings:
        imdb_id = (row.get("imdbID") or {}).get("value")
        if not imdb_id:
            continue
        record = by_imdb_id.setdefault(imdb_id, {"production_countries": [], "poster": {}})
        country_code = (row.get("countryCode") or {}).get("value", "").upper()
        if len(country_code) == 2 and country_code not in record["production_countries"]:
            record["production_countries"].append(country_code)
        image = (row.get("image") or {}).get("value")
        image_data = poster_fields(image)
        if image_data and not record["poster"]:
            record["poster"] = image_data

    result = {}
    for movie in movies:
        imdb_id = str(movie.get("imdb_id") or "")
        metadata = by_imdb_id.get(imdb_id)
        if not imdb_id or not metadata:
            continue
        record = {
            "id": movie.get("id"),
            "imdb_id": imdb_id,
            "title": movie.get("title", ""),
        }
        if metadata["production_countries"]:
            record["production_countries"] = metadata["production_countries"]
            record["country_source"] = "Wikidata property P495"
        record.update(metadata["poster"])
        if len(record) > 3:
            result[imdb_id] = record
    return result


def records_from_title_bindings(movies, bindings):
    by_title_year = {}
    for row in bindings:
        title = (row.get("requestedTitle") or {}).get("value")
        year = (row.get("requestedYear") or {}).get("value")
        if not title or not year:
            continue
        key = (title.casefold(), int(year))
        record = by_title_year.setdefault(key, {"production_countries": [], "poster": {}})
        country_code = (row.get("countryCode") or {}).get("value", "").upper()
        if len(country_code) == 2 and country_code not in record["production_countries"]:
            record["production_countries"].append(country_code)
        image_data = poster_fields((row.get("image") or {}).get("value"))
        if image_data and not record["poster"]:
            record["poster"] = image_data

    result = {}
    for movie in movies:
        key = ((movie.get("title") or "").casefold(), int(movie.get("release_year") or 0))
        metadata = by_title_year.get(key)
        if not metadata:
            continue
        record = {"id": movie.get("id"), "title": movie.get("title", "")}
        if metadata["production_countries"]:
            record["production_countries"] = metadata["production_countries"]
            record["country_source"] = "Wikidata property P495; exact title and year"
        record.update(metadata["poster"])
        if len(record) > 2:
            result[f"id:{movie.get('id')}"] = record
    return result


def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def enrich_deployment_catalog():
    from data.build_embeddings import deployment_movies

    movies = deployment_movies()
    progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8")) if PROGRESS_PATH.is_file() else {}
    if not isinstance(progress, dict):
        progress = {}
    existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) if OUTPUT_PATH.is_file() else []
    for record in existing:
        if record.get("imdb_id"):
            progress.setdefault(str(record["imdb_id"]), record)
        elif record.get("id") is not None:
            progress.setdefault(f"id:{record['id']}", record)

    remaining = [
        movie for movie in movies
        if movie.get("imdb_id") and (
            str(movie["imdb_id"]) not in progress
            or not progress.get(str(movie["imdb_id"]))
            or progress.get(str(movie["imdb_id"]), {}).get("_retry")
        )
    ]
    for start in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[start:start + BATCH_SIZE]
        try:
            bindings = fetch_metadata([movie["imdb_id"] for movie in batch])
        except Exception as exc:
            for movie in batch:
                progress[str(movie["imdb_id"])] = {
                    "_retry": True,
                    "error": type(exc).__name__,
                }
            write_json_atomic(PROGRESS_PATH, progress)
            print(f"Skipped IMDb batch at {start:,}: {type(exc).__name__}; marked for retry.")
            continue
        progress.update(records_from_bindings(batch, bindings))
        for movie in batch:
            progress.setdefault(str(movie["imdb_id"]), {})
        write_json_atomic(PROGRESS_PATH, progress)
        print(f"Checked {min(start + len(batch), len(remaining)):,}/{len(remaining):,} remaining IMDb IDs")
        if start + BATCH_SIZE < len(remaining):
            time.sleep(REQUEST_DELAY_SECONDS)

    curated_remaining = [
        movie for movie in movies
        if not movie.get("imdb_id") and movie.get("title") and movie.get("release_year")
        and (
            f"id:{movie.get('id')}" not in progress
            or not progress.get(f"id:{movie.get('id')}")
            or progress.get(f"id:{movie.get('id')}", {}).get("_retry")
        )
    ]
    title_batch_size = 1
    for start in range(0, len(curated_remaining), title_batch_size):
        batch = curated_remaining[start:start + title_batch_size]
        try:
            bindings = fetch_title_metadata(batch)
        except Exception as exc:
            progress[f"id:{batch[0].get('id')}"] = {
                "_retry": True,
                "error": type(exc).__name__,
            }
            write_json_atomic(PROGRESS_PATH, progress)
            print(f"Skipped curated title at {start:,}: {type(exc).__name__}; marked for retry.")
            continue
        progress.update(records_from_title_bindings(batch, bindings))
        for movie in batch:
            progress.setdefault(f"id:{movie.get('id')}", {})
        write_json_atomic(PROGRESS_PATH, progress)
        print(f"Checked {min(start + len(batch), len(curated_remaining)):,}/{len(curated_remaining):,} curated records")
        if start + title_batch_size < len(curated_remaining):
            time.sleep(1.2)

    by_id = {
        str(movie.get("imdb_id")) if movie.get("imdb_id") else f"id:{movie.get('id')}": movie
        for movie in movies
    }
    records = [
        {**{key: movie.get(key) for key in ("id", "imdb_id", "title") if movie.get(key) is not None}, **metadata}
        for record_key, metadata in progress.items()
        if (movie := by_id.get(record_key)) is not None
        and isinstance(metadata, dict)
        and metadata
        and not metadata.get("_retry")
    ]
    write_json_atomic(OUTPUT_PATH, records)
    retry_count = sum(1 for metadata in progress.values() if isinstance(metadata, dict) and metadata.get("_retry"))
    if retry_count:
        write_json_atomic(PROGRESS_PATH, progress)
    else:
        PROGRESS_PATH.unlink(missing_ok=True)
    poster_count = sum(bool(record.get("poster_url")) for record in records)
    country_count = sum(bool(record.get("production_countries")) for record in records)
    print(
        f"Wrote {len(records):,} records with public metadata; "
        f"{poster_count:,} Commons poster images and {country_count:,} production-country records; "
        f"{retry_count:,} records marked for retry."
    )
    return records


if __name__ == "__main__":
    enrich_deployment_catalog()
