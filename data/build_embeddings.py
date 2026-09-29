"""Build optional OpenAI embeddings for the compact deployable catalog."""

import ast
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


if __name__ == "__main__":
    movies = curated_movies() + GENERATED_MOVIES
    output = build_movie_embeddings(movies)
    print(f"Wrote embeddings for {len(movies):,} movies to {output}")