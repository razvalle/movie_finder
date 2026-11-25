"""Build a local TF-IDF index for the compact deployable catalog."""

import ast
import json
import pickle
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from data.generated_movies import GENERATED_MOVIES
from data.movies import MOVIES
from data.catalog_version import catalog_version
from data.build_concept_expansions import build_concept_table
from nlp.concepts import CONCEPT_TABLE_PATH, load_concept_table
from nlp.tfidf import (
    load_or_build_tfidf_model,
    movie_to_similarity_document_text,
)


def curated_movies():
    source = ast.parse((ROOT_DIR / "data" / "movies.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MOVIES" for target in node.targets)
    )
    return ast.literal_eval(assignment.value)


def deployment_movies():
    movies = curated_movies() + GENERATED_MOVIES
    enrichment_path = ROOT_DIR / "data" / "movie_cast_enrichment.json"
    if enrichment_path.is_file():
        enriched = json.loads(enrichment_path.read_text(encoding="utf-8"))
        by_key = {
            str(movie.get("imdb_id") or f"id:{movie.get('id')}"): movie
            for movie in enriched
        }
        for movie in movies:
            key = str(movie.get("imdb_id") or f"id:{movie.get('id')}")
            if key in by_key:
                movie.update(by_key[key])
    return movies


if __name__ == "__main__":
    movies = MOVIES
    data_directory = ROOT_DIR / "data"
    catalog_path = data_directory / "imdb_movies.json"
    tfidf_path = data_directory / "tfidf_cache.pkl"
    like_tfidf_path = data_directory / "tfidf_like_cache.pkl"
    version = catalog_version(movies)

    tfidf_model = load_or_build_tfidf_model(movies, tfidf_path, catalog_path)
    like_model = load_or_build_tfidf_model(
        movies,
        like_tfidf_path,
        catalog_path,
        document_text_builder=movie_to_similarity_document_text,
    )
    concept_table = build_concept_table(movies)
    CONCEPT_TABLE_PATH.write_text(
        json.dumps(concept_table, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for path in (tfidf_path, like_tfidf_path):
        if not path.is_file():
            raise SystemExit(f"Required TF-IDF index was not created: {path}")
        with path.open("rb") as stream:
            index = pickle.load(stream)
        if (
            index.get("catalog_version") != version
            or index.get("index_count") != len(movies)
            or len(index.get("model", {}).get("vectors", {})) != len(movies)
        ):
            raise SystemExit(f"TF-IDF index failed catalog validation: {path}")
    if not tfidf_model or not like_model or not load_concept_table(movies)["enabled"]:
        raise SystemExit("Required catalog search indexes failed validation.")

    print(
        f"Wrote versioned search indexes for {len(movies):,} movies "
        f"(catalog hash {version['content_hash'][:12]}) to {data_directory}"
    )