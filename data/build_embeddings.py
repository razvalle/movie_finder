"""Build a local TF-IDF index for the compact deployable catalog."""

import ast
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from data.generated_movies import GENERATED_MOVIES
from nlp.smart_search import build_movie_embeddings


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
    movies = deployment_movies()
    output = build_movie_embeddings(movies)
    print(f"Wrote local TF-IDF index for {len(movies):,} movies to {output}")