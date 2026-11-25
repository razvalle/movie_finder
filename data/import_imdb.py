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
import datetime
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.audit_catalog import clean_catalog

csv.field_size_limit(10_000_000)

DATA_DIR = Path(__file__).resolve().parent
IMDB_DIR = DATA_DIR / "imdb"
BASICS_PATH = IMDB_DIR / "title.basics.tsv.gz"
RATINGS_PATH = IMDB_DIR / "title.ratings.tsv.gz"
AKAS_PATH = IMDB_DIR / "title.akas.tsv.gz"
OUTPUT_PATH = DATA_DIR / "imdb_movies.json"
START_YEAR = 1990
END_YEAR = datetime.date.today().year


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
    audit_path = DATA_DIR / "imdb_import_audit.jsonl"
    with gzip.open(BASICS_PATH, "rt", encoding="utf-8", newline="") as stream, \
            audit_path.open("w", encoding="utf-8") as audit_log:
        for row in csv.DictReader(stream, delimiter="\t"):
            source_record = {
                "imdb_id": row.get("tconst"),
                "title": row.get("primaryTitle"),
                "original_title": row.get("originalTitle"),
                "title_type": row.get("titleType"),
                "is_adult": row.get("isAdult"),
                "release_year": row.get("startYear"),
                "end_year": row.get("endYear"),
                "runtime": row.get("runtimeMinutes"),
                "genres": row.get("genres"),
            }
            if row["titleType"] != "movie":
                audit_log.write(json.dumps({
                    "action": "remove",
                    "reason": "non-movie title_type",
                    "record_id": row.get("tconst"),
                    "record": source_record,
                }, ensure_ascii=False) + "\n")
                continue
            if row["isAdult"] == "1":
                audit_log.write(json.dumps({
                    "action": "remove",
                    "reason": "adult-flagged record",
                    "record_id": row.get("tconst"),
                    "record": source_record,
                }, ensure_ascii=False) + "\n")
                continue
            year = None if row["startYear"] == "\\N" else int(row["startYear"])
            if year is not None and year < START_YEAR:
                audit_log.write(json.dumps({
                    "action": "remove",
                    "reason": "outside configured year floor",
                    "record_id": row.get("tconst"),
                    "record": source_record,
                }, ensure_ascii=False) + "\n")
                continue
            if year is not None and year > END_YEAR:
                audit_log.write(json.dumps({
                    "action": "normalize",
                    "reason": "future release year set to unknown",
                    "record_id": row.get("tconst"),
                    "record": source_record,
                    "changes": {"release_year": {"from": year, "to": None}},
                }, ensure_ascii=False) + "\n")
                year = None
            genres = values(row["genres"])
            runtime = None if row["runtimeMinutes"] == "\\N" else int(row["runtimeMinutes"])
            rating = ratings.get(row["tconst"], {})
            title = row["primaryTitle"]
            genre_text = ", ".join(genres)
            catalog.append({
                "id": len(catalog) + 1,
                "imdb_id": row["tconst"],
                "title": title,
                "original_title": row["originalTitle"],
                "title_type": row["titleType"],
                "is_adult": False,
                "synopsis": (
                    f"{title} ({year}), a {genre_text.lower()} movie."
                    if year is not None
                    else f"{title}, a {genre_text.lower()} movie."
                ),
                "genres": [genre.lower() for genre in genres],
                "runtime": runtime,
                "release_year": year,
                "themes": [],
                "mood_tags": [],
                "keywords": [title, *genres],
                "content_descriptors": [],
                "average_rating": rating.get("average_rating"),
                "vote_count": rating.get("vote_count", 0),
            })

    regions = load_regions({movie["imdb_id"] for movie in catalog})
    for movie in catalog:
        movie["origin_regions"] = sorted(regions.get(movie["imdb_id"], set()))
        movie["nationality"] = movie["origin_regions"][0] if movie["origin_regions"] else None

    catalog, cleaning_events = clean_catalog(catalog, current_year=END_YEAR)
    with audit_path.open("a", encoding="utf-8") as audit_log:
        for event in cleaning_events:
            audit_log.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    catalog.sort(key=lambda movie: (
        movie.get("release_year") or 0,
        movie["title"].lower(),
        movie["imdb_id"],
    ))
    for index, movie in enumerate(catalog, start=1):
        movie["id"] = index
    OUTPUT_PATH.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return catalog


if __name__ == "__main__":
    catalog = build_catalog()
    print(f"Wrote {len(catalog):,} IMDb movies to {OUTPUT_PATH}")
