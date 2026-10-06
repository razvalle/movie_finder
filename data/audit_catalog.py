"""Audit the deployment catalog: per-field coverage, duplicates, malformed records.

Run from the repository root:
    python data/audit_catalog.py              # deployment catalog (curated + generated + enrichment)
    python data/audit_catalog.py --catalog local   # whatever data/movies.py loads
    python data/audit_catalog.py --json report.json
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

# Synopses produced by import_imdb.py / build_popular_movies.py are metadata templates, not plots.
TEMPLATE_SYNOPSIS = re.compile(
    r"^IMDb rating .* from [\d,]+ votes; released in \d{4}\. Genres:|\), a .* movie\.$"
)
YEAR_RANGE = (1880, 2100)
RUNTIME_RANGE = (1, 600)


def normalized_title(title):
    return re.sub(r"[^a-z0-9]+", "", str(title).casefold()).strip()


def load_catalog(kind):
    if kind == "local":
        from data.movies import MOVIES
        return list(MOVIES)
    from data.build_embeddings import deployment_movies
    movies = deployment_movies()
    public_path = ROOT_DIR / "data" / "movie_public_enrichment.json"
    if public_path.is_file():
        by_key = {str(m.get("imdb_id") or f"id:{m.get('id')}"): m for m in json.loads(public_path.read_text(encoding="utf-8"))}
        for movie in movies:
            record = by_key.get(str(movie.get("imdb_id") or f"id:{movie.get('id')}"))
            for key, value in (record or {}).items():
                if not movie.get(key) and value:
                    movie[key] = value
    return movies


def has_real_plot(movie):
    synopsis = movie.get("synopsis") or ""
    return bool(synopsis) and not TEMPLATE_SYNOPSIS.search(synopsis)


FIELD_CHECKS = {
    "synopsis (any)": lambda m: bool(m.get("synopsis")),
    "synopsis (real plot, not template)": has_real_plot,
    "original_title": lambda m: bool(m.get("original_title")),
    "original_title differs from title": lambda m: bool(m.get("original_title")) and str(m["original_title"]).strip() != str(m.get("title") or "").strip(),
    "IMDb id": lambda m: bool(m.get("imdb_id")),
    "IMDb keywords": lambda m: bool(m.get("keywords")),
    "IMDb release regions": lambda m: bool(m.get("origin_regions")),
    "IMDb adult flag present": lambda m: "is_adult" in m,
    "runtime": lambda m: isinstance(m.get("runtime"), int) and m["runtime"] > 0,
    "release_year": lambda m: isinstance(m.get("release_year"), int),
    "genres": lambda m: bool(m.get("genres")),
    "rating": lambda m: m.get("average_rating") is not None,
    "themes": lambda m: bool(m.get("themes")),
    "mood_tags": lambda m: bool(m.get("mood_tags")),
    "content_descriptors": lambda m: bool(m.get("content_descriptors")),
    "cast": lambda m: bool(m.get("cast")),
    "poster": lambda m: bool(m.get("poster_path") or m.get("poster_url")),
    "certification": lambda m: bool(m.get("certification")),
    "production_countries": lambda m: bool(m.get("production_countries")),
    "original_language": lambda m: bool(m.get("original_language")),
}


def malformed_reasons(movie):
    reasons = []
    if not str(movie.get("title") or "").strip():
        reasons.append("missing title")
    year = movie.get("release_year")
    if not isinstance(year, int) or not YEAR_RANGE[0] <= year <= YEAR_RANGE[1]:
        reasons.append(f"invalid release_year {year!r}")
    runtime = movie.get("runtime")
    if runtime is not None and (not isinstance(runtime, int) or not RUNTIME_RANGE[0] <= runtime <= RUNTIME_RANGE[1]):
        reasons.append(f"runtime out of range {runtime!r}")
    if not isinstance(movie.get("genres"), list) or not movie.get("genres"):
        reasons.append("missing genres")
    rating = movie.get("average_rating")
    if rating is not None and not (isinstance(rating, (int, float)) and 0 <= rating <= 10):
        reasons.append(f"rating out of range {rating!r}")
    return reasons


def audit(movies):
    total = len(movies)
    coverage = {name: sum(1 for m in movies if check(m)) for name, check in FIELD_CHECKS.items()}

    ids = Counter(m.get("id") for m in movies)
    imdb_ids = Counter(m["imdb_id"] for m in movies if m.get("imdb_id"))
    title_year = Counter((normalized_title(m.get("title")), m.get("release_year")) for m in movies)
    duplicates = {
        "duplicate_ids": sorted(str(k) for k, v in ids.items() if v > 1),
        "duplicate_imdb_ids": sorted(k for k, v in imdb_ids.items() if v > 1),
        "duplicate_title_year": sorted(f"{t} ({y})" for (t, y), v in title_year.items() if v > 1),
    }
    malformed = [
        {"id": m.get("id"), "title": m.get("title"), "problems": reasons}
        for m in movies if (reasons := malformed_reasons(m))
    ]
    tmdb_key_configured = bool(os.environ.get("TMDB_API_KEY", "").strip())
    return {
        "total": total,
        "coverage": coverage,
        "duplicates": duplicates,
        "malformed": malformed,
        "metadata_status": {
            "tmdb_key_configured": tmdb_key_configured,
            "enrichment": "opt-in; inactive without TMDB_API_KEY" if not tmdb_key_configured else "opt-in; run an enrichment script explicitly",
            "fabrication": "No plot, cast, or content-descriptor metadata has been fabricated to fill gaps. IMDb-derived synopsis templates are not plot descriptions.",
            "synthetic_benchmark_priority": "Generated held-out queries point to real plot/keyword coverage as the next metadata priority. They do not test cast or content-descriptor searches, so those fields cannot be prioritized from this benchmark. This is synthetic evidence, not user feedback.",
        },
    }


def format_report(report):
    total = report["total"] or 1
    lines = [f"Records: {report['total']:,}", "", "Field coverage:"]
    for name, count in report["coverage"].items():
        lines.append(f"  {name:<36} {count:>7,}  {count / total:6.1%}")
    lines += ["", "Duplicates:"]
    for name, values in report["duplicates"].items():
        lines.append(f"  {name}: {len(values)}" + (f"  e.g. {values[:3]}" if values else ""))
    lines += ["", f"Malformed records: {len(report['malformed'])}"]
    for item in report["malformed"][:10]:
        lines.append(f"  id={item['id']} {item['title']!r}: {'; '.join(item['problems'])}")
    status = report["metadata_status"]
    lines += [
        "",
        "Metadata limitation:",
        "  Plots, cast, and content descriptors are limited to locally imported/enriched fields.",
        f"  TMDB key configured: {'yes' if status['tmdb_key_configured'] else 'no'}; enrichment is opt-in and otherwise inactive.",
        f"  {status['fabrication']}",
        f"  Synthetic benchmark only: {status['synthetic_benchmark_priority']}",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", choices=("deployment", "local"), default="deployment")
    parser.add_argument("--json", help="also write the full report to this path")
    args = parser.parse_args()
    report = audit(load_catalog(args.catalog))
    print(format_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
