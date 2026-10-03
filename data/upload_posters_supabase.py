"""Upload and verify catalog JPEGs in the configured Supabase public bucket.

Run from the project root after applying supabase/migrations/001_storage.sql:
    python -m data.upload_posters_supabase
    python -m data.upload_posters_supabase --verify-only
"""

import argparse
import ast
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from poster_storage import poster_public_url, upload_poster, verify_public_poster


IMAGE_DIR = Path(__file__).resolve().parents[1] / "static" / "images"


def _movie_poster_files():
    project_root = Path(__file__).resolve().parents[1]
    source_tree = ast.parse((project_root / "data" / "movies.py").read_text(encoding="utf-8"))
    core_movies = next(
        ast.literal_eval(node.value)
        for node in source_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MOVIES" for target in node.targets)
    )
    popular_movies = json.loads((project_root / "data" / "popular_movies.json").read_text(encoding="utf-8"))
    movies = core_movies + popular_movies
    files = []
    missing = []
    for movie in movies:
        path = IMAGE_DIR / f"{movie['id']}.jpg"
        if path.is_file():
            files.append(path)
        else:
            missing.append(f"{movie['id']}: {movie['title']}")
    if missing:
        raise RuntimeError("Missing local posters:\n" + "\n".join(missing[:20]))
    return files


def _process(path, verify_only):
    public_url = poster_public_url(path.stem)
    if not verify_only:
        public_url = upload_poster(path)
    if not verify_public_poster(public_url, path.stat().st_size):
        raise RuntimeError(f"Public poster verification failed for movie ID {path.stem}.")
    return path.name, public_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Check public objects without uploading.")
    args = parser.parse_args()
    if not args.verify_only and not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        parser.error("Set SUPABASE_SERVICE_ROLE_KEY in the environment; never commit it.")
    if not os.environ.get("SUPABASE_URL"):
        parser.error("Set SUPABASE_URL in the environment.")

    files = _movie_poster_files()
    workers = max(1, min(6, int(os.environ.get("POSTER_UPLOAD_WORKERS", "4"))))
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process, path, args.verify_only): path for path in files}
        for future in as_completed(futures):
            path = futures[future]
            try:
                name, public_url = future.result()
                print(f"OK {name} {public_url}")
            except Exception as error:
                failures.append((path.name, str(error)))
                print(f"FAILED {path.name}: {error}")

    print(f"Verified {len(files) - len(failures)} of {len(files)} posters.")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()