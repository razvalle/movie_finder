"""Enrich the local IMDb catalog with real TMDB plot overviews and posters.

Setup:
    $env:TMDB_API_KEY = "your_tmdb_v3_api_key"
    python data/enrich_tmdb.py

The script is resumable. It writes a temporary JSON file after each batch,
keeps IMDb metadata intact, and only replaces generated metadata synopses
when TMDB supplies a real overview. It uses no AI or recommendation service;
TMDB is used only as a movie metadata source.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
CATALOG_PATH = DATA_DIR / "imdb_movies.json"
TEMP_PATH = DATA_DIR / "imdb_movies.tmdb_progress.json"
POSTER_DIR = DATA_DIR.parent / "static" / "images"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
BATCH_SIZE = 25
REQUEST_DELAY_SECONDS = 0.25


def request_json(url, api_key):
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def find_tmdb_movie(imdb_id, api_key):
    encoded_id = urllib.parse.quote(imdb_id)
    url = f"{TMDB_BASE_URL}/find/{encoded_id}?api_key={urllib.parse.quote(api_key)}&external_source=imdb_id"
    payload = request_json(url, api_key)
    results = payload.get("movie_results", [])
    return results[0] if results else None


def enrich_catalog():
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set the TMDB_API_KEY environment variable before running this script.")
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Missing catalog: {CATALOG_PATH}")

    source_path = TEMP_PATH if TEMP_PATH.exists() else CATALOG_PATH
    catalog = json.loads(source_path.read_text(encoding="utf-8"))
    enriched = 0
    failed = 0

    for index, movie in enumerate(catalog):
        if movie.get("plot_source") == "TMDB" and movie.get("synopsis"):
            continue
        imdb_id = movie.get("imdb_id")
        if not imdb_id:
            continue
        try:
            result = find_tmdb_movie(imdb_id, api_key)
            if result:
                overview = (result.get("overview") or "").strip()
                if overview:
                    movie["synopsis"] = overview
                    movie["plot_source"] = "TMDB"
                    movie["tmdb_id"] = result.get("id")
                    enriched += 1
                poster_path = result.get("poster_path")
                if poster_path:
                    movie["poster_path"] = poster_path
                production_countries = [
                    country["iso_3166_1"]
                    for country in result.get("production_countries", [])
                    if country.get("iso_3166_1")
                ]
                if production_countries:
                    movie["production_countries"] = production_countries
                    movie["country_source"] = "TMDB production country"
            time.sleep(REQUEST_DELAY_SECONDS)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
            failed += 1

        if (index + 1) % BATCH_SIZE == 0:
            TEMP_PATH.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
            print(f"Processed {index + 1:,}/{len(catalog):,}; enriched {enriched:,}; failed {failed:,}")

    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    TEMP_PATH.unlink(missing_ok=True)
    print(f"Enriched {enriched:,} movies with real TMDB overviews; {failed:,} requests failed.")


if __name__ == "__main__":
    enrich_catalog()
