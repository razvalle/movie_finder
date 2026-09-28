"""Load the compact, real-title catalog used when the full IMDb export is absent."""

import json
from pathlib import Path


POPULAR_CATALOG_PATH = Path(__file__).with_name("popular_movies.json")
GENERATED_MOVIES = json.loads(POPULAR_CATALOG_PATH.read_text(encoding="utf-8"))
