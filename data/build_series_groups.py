"""Derive and review catalog series groups without search-code examples."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.movies import MOVIES
from nlp.catalog_matching import derive_series_groups


if __name__ == "__main__":
    groups = derive_series_groups(MOVIES)
    output = ROOT / "data" / "series_groups.json"
    output.write_text(json.dumps(groups, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(groups)} detected series to {output}")
