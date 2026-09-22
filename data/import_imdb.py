"""Build the local movie catalog from IMDb's public TSV datasets.

Download title.basics.tsv.gz and title.ratings.tsv.gz from:
https://datasets.imdbws.com/

Then run:
    python data/import_imdb.py

The output is data/imdb_movies.json and is loaded automatically by
movies.py when present. IMDb supplies metadata, not plot synopses; the
searchable synopsis is therefore a concise metadata description.
"""

import csv
import gzip
import json
from pathlib import Path

csv.field_size_limit(10_000_000)

DATA_DIR = Path(__file__).resolve().parent
IMDB_DIR = DATA_DIR / "imdb"
BASICS_PATH = IMDB_DIR / "title.basics.tsv.gz"
RATINGS_PATH = IMDB_DIR / "title.ratings.tsv.gz"
AKAS_PATH = IMDB_DIR / "title.akas.tsv.gz"
OUTPUT_PATH = DATA_DIR / "imdb_movies.json"
START_YEAR = 2000
END_YEAR = 2026


def values(value):
    return [] if value == "\\N" else [item for item in value.split(",") if item]


def load_ratings():
    if not RATINGS_PATH.exists():
        return {}
    with gzip.open(RATINGS_PATH, "rt", encoding="utf-8", newline="") as stream:
        return {
            row["tconst"]: {
                "average_rating": float(row["averageRating"]),
                "vote_count": int(row["numVotes"]),
            }
            for row in csv.DictReader(stream, delimiter="\t")
        }


def load_regions(title_ids):
    """Load release-region codes only for the selected movie IDs."""
    regions = {}
    if not AKAS_PATH.exists():
        return regions
    with gzip.open(AKAS_PATH, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            title_id = row["titleId"]
            region = row["region"]
            if title_id in title_ids and region != "\\N":
                regions.setdefault(title_id, set()).add(region)
    return regions


def build_catalog():
    if not BASICS_PATH.exists():
        raise FileNotFoundError(f"Missing IMDb dataset: {BASICS_PATH}")

    ratings = load_ratings()
    catalog = []
    with gzip.open(BASICS_PATH, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["titleType"] != "movie":
                continue
            if row["startYear"] == "\\N":
                continue
            year = int(row["startYear"])
            if not START_YEAR <= year <= END_YEAR:
                continue
            genres = values(row["genres"])
            if not genres:
                continue
            runtime = None if row["runtimeMinutes"] == "\\N" else int(row["runtimeMinutes"])
            if runtime is None:
                runtime = 0
            rating = ratings.get(row["tconst"], {})
            title = row["primaryTitle"]
            genre_text = ", ".join(genres)
            catalog.append({
                "id": len(catalog) + 1,
                "imdb_id": row["tconst"],
                "title": title,
                "original_title": row["originalTitle"],
                "title_type": row["titleType"],
                "is_adult": row["isAdult"] == "1",
                "synopsis": f"{title} ({year}), a {genre_text.lower()} movie.",
                "genres": [genre.lower() for genre in genres],
                "runtime": runtime,
                "release_year": year,
                "themes": [],
                "mood_tags": [],
                "keywords": [title, *genres],
                "content_descriptors": ["adult"] if row["isAdult"] == "1" else [],
                "average_rating": rating.get("average_rating"),
                "vote_count": rating.get("vote_count", 0),
            })

    regions = load_regions({movie["imdb_id"] for movie in catalog})
    for movie in catalog:
        movie["origin_regions"] = sorted(regions.get(movie["imdb_id"], set()))
        movie["nationality"] = movie["origin_regions"][0] if movie["origin_regions"] else "Unknown"

    catalog.sort(key=lambda movie: (movie["release_year"], movie["title"].lower(), movie["imdb_id"]))
    for index, movie in enumerate(catalog, start=1):
        movie["id"] = index
    OUTPUT_PATH.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return catalog


if __name__ == "__main__":
    catalog = build_catalog()
    print(f"Wrote {len(catalog):,} IMDb movies to {OUTPUT_PATH}")
