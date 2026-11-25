"""Audit the deployment catalog: per-field coverage, duplicates, malformed records.

Run from the repository root:
    python data/audit_catalog.py              # deployment catalog (curated + generated + enrichment)
    python data/audit_catalog.py --catalog local   # whatever data/movies.py loads
    python data/audit_catalog.py --json report.json
"""

import argparse
import datetime
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

# Synopses produced by import_imdb.py / build_popular_movies.py are metadata templates, not plots.
TEMPLATE_SYNOPSIS = re.compile(
    r"^IMDb rating .* from [\d,]+ votes; released in \d{4}\. Genres:|\), a .* movie\.$"
)
YEAR_RANGE = (1880, datetime.date.today().year)
RUNTIME_RANGE = (1, 600)
AUDIT_LOG_PATH = ROOT_DIR / "data" / "catalog_audit_log.jsonl"
LIST_FIELDS = {
    "genres", "themes", "mood_tags", "keywords", "content_descriptors",
    "production_countries", "origin_regions", "notable_awards", "cast", "actors",
}


def _clean_text(value):
    if value == "\\N":
        return None
    cleaned = unicodedata.normalize("NFC", value)
    if any(marker in cleaned for marker in ("Ã", "â", "ð", "ï»¿", "Â")):
        for encoding in ("cp1252", "latin-1"):
            try:
                repaired = cleaned.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if repaired != cleaned:
                cleaned = repaired
                break
    cleaned = " ".join(cleaned.split())
    return cleaned or None


def _clean_value(value):
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, list):
        cleaned = [_clean_value(item) for item in value]
        return [item for item in cleaned if item not in (None, "")]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            value = _clean_value(item)
            cleaned[key] = None if value == "" else value
        return cleaned
    return value


def _normalized_genre(value):
    from data.synonyms import GENRE_SYNONYMS

    def key(text):
        return " ".join(re.findall(r"[a-z0-9]+", str(text).casefold()))

    normalized = key(value)
    for canonical, aliases in GENRE_SYNONYMS.items():
        if normalized in {key(canonical), *(key(alias) for alias in aliases)}:
            return canonical
    return normalized


def _adult_flagged(movie):
    value = movie.get("is_adult", movie.get("isAdult", movie.get("adult")))
    return value is True or str(value).strip().casefold() in {"1", "true", "yes"}


def _record_type(movie):
    value = movie.get("title_type", movie.get("titleType", movie.get("type")))
    return str(value).strip().casefold() if value is not None else ""


def _event(action, reason, record, **details):
    return {
        "action": action,
        "reason": reason,
        "record_id": record.get("imdb_id", record.get("id")) if isinstance(record, dict) else None,
        "record": record,
        **details,
    }


def _dedupe_key(movie):
    stable_id = movie.get("imdb_id") or movie.get("id")
    if stable_id is not None and str(stable_id).strip():
        field = "imdb_id" if movie.get("imdb_id") else "id"
        return field, str(stable_id).strip().casefold()
    title = normalized_title(movie.get("title"))
    year, runtime = movie.get("release_year"), movie.get("runtime")
    if title and year is not None and runtime is not None:
        return "title-year-runtime", title, year, runtime
    return None


def _merge_record(target, duplicate):
    for field, value in duplicate.items():
        if field not in target or target[field] in (None, "", []):
            target[field] = value
        elif isinstance(target[field], list) and isinstance(value, list):
            known = {
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in target[field]
            }
            for item in value:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if key not in known:
                    target[field].append(item)
                    known.add(key)


def clean_catalog(movies, current_year=None):
    """Apply deterministic import rules and return cleaned records plus an event log."""
    current_year = current_year or datetime.date.today().year
    cleaned, events, by_key = [], [], {}
    for raw in movies:
        if not isinstance(raw, dict):
            events.append(_event("remove", "record is not an object", {"value": raw}))
            continue
        title_type = _record_type(raw)
        if title_type and title_type != "movie":
            events.append(_event("remove", "non-movie title_type", raw, title_type=title_type))
            continue
        if _adult_flagged(raw):
            events.append(_event("remove", "adult-flagged record", raw))
            continue

        movie = _clean_value(raw)
        changes = {}
        if movie != raw:
            for field in sorted(set(movie) | set(raw)):
                if movie.get(field) != raw.get(field):
                    changes[field] = {"from": raw.get(field), "to": movie.get(field)}

        title = movie.get("title")
        if not isinstance(title, str) or not title:
            events.append(_event("remove", "missing title", raw))
            continue

        runtime = movie.get("runtime")
        if (
            isinstance(runtime, bool)
            or not isinstance(runtime, int)
            or not RUNTIME_RANGE[0] <= runtime <= RUNTIME_RANGE[1]
        ):
            if runtime is not None:
                changes["runtime"] = {"from": runtime, "to": None}
            movie["runtime"] = None

        year = movie.get("release_year")
        if (
            isinstance(year, bool)
            or not isinstance(year, int)
            or not YEAR_RANGE[0] <= year <= current_year
        ):
            if year is not None:
                changes["release_year"] = {"from": year, "to": None}
            movie["release_year"] = None

        genres = movie.get("genres")
        if isinstance(genres, str):
            genres = [genres]
        if not isinstance(genres, list):
            genres = []
        normalized_genres = []
        for genre in genres:
            if not isinstance(genre, str) or not genre.strip():
                continue
            canonical = _normalized_genre(genre)
            if canonical and canonical not in normalized_genres:
                normalized_genres.append(canonical)
        if normalized_genres != movie.get("genres"):
            changes["genres"] = {
                "from": movie.get("genres"),
                "to": normalized_genres,
            }
        movie["genres"] = normalized_genres
        if changes:
            events.append(_event("normalize", "field normalization", raw, changes=changes))

        key = _dedupe_key(movie)
        if key is not None and key in by_key:
            kept = by_key[key]
            _merge_record(kept, movie)
            events.append(_event(
                "merge", "duplicate catalog identity", raw,
                merged_into=kept.get("imdb_id", kept.get("id")),
                identity=key,
            ))
            continue
        cleaned.append(movie)
        if key is not None:
            by_key[key] = movie
    return cleaned, events


def write_catalog_audit_log(events, path=AUDIT_LOG_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False, default=str) + "\n" for event in events),
        encoding="utf-8",
    )


def normalized_title(title):
    return re.sub(r"[^a-z0-9]+", "", str(title).casefold()).strip()


def load_catalog(kind):
    if kind == "local":
        from data.movies import CATALOG_CLEANING_EVENTS, MOVIES
        return list(MOVIES), list(CATALOG_CLEANING_EVENTS)
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
    return movies, []


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
    if year is not None and (
        isinstance(year, bool)
        or not isinstance(year, int)
        or not YEAR_RANGE[0] <= year <= YEAR_RANGE[1]
    ):
        reasons.append(f"invalid release_year {year!r}")
    runtime = movie.get("runtime")
    if runtime is not None and (
        isinstance(runtime, bool)
        or not isinstance(runtime, int)
        or not RUNTIME_RANGE[0] <= runtime <= RUNTIME_RANGE[1]
    ):
        reasons.append(f"runtime out of range {runtime!r}")
    if not isinstance(movie.get("genres"), list) or not movie.get("genres"):
        reasons.append("missing genres")
    rating = movie.get("average_rating")
    if rating is not None and not (isinstance(rating, (int, float)) and 0 <= rating <= 10):
        reasons.append(f"rating out of range {rating!r}")
    return reasons


def audit(movies, cleaning_events=()):
    total = len(movies)
    coverage = {name: sum(1 for m in movies if check(m)) for name, check in FIELD_CHECKS.items()}

    ids = Counter(m.get("id") for m in movies)
    imdb_ids = Counter(m["imdb_id"] for m in movies if m.get("imdb_id"))
    title_year_runtime = Counter(
        (
            normalized_title(m.get("title")),
            m.get("release_year"),
            m.get("runtime"),
        )
        for m in movies
        if m.get("title") and m.get("release_year") is not None
        and m.get("runtime") is not None
    )
    duplicates = {
        "duplicate_ids": sorted(str(k) for k, v in ids.items() if v > 1),
        "duplicate_imdb_ids": sorted(k for k, v in imdb_ids.items() if v > 1),
        "duplicate_title_year_runtime": sorted(
            f"{title} ({year}, {runtime} min)"
            for (title, year, runtime), count in title_year_runtime.items()
            if count > 1
        ),
    }
    malformed = [
        {"id": m.get("id"), "title": m.get("title"), "problems": reasons}
        for m in movies if (reasons := malformed_reasons(m))
    ]
    events = list(cleaning_events)
    unverified = sum(
        not movie.get("runtime")
        or not movie.get("release_year")
        or not movie.get("genres")
        for movie in movies
    )
    cleaning = {
        "removed": sum(event.get("action") == "remove" for event in events),
        "merged": sum(event.get("action") == "merge" for event in events),
        "normalized": sum(event.get("action") == "normalize" for event in events),
        "unverified": unverified,
        "event_reasons": dict(Counter(
            event.get("reason", "unknown") for event in events
        )),
    }
    tmdb_key_configured = bool(os.environ.get("TMDB_API_KEY", "").strip())
    return {
        "total": total,
        "coverage": coverage,
        "duplicates": duplicates,
        "malformed": malformed,
        "cleaning": cleaning,
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
    cleaning = report["cleaning"]
    lines += [
        "",
        "Catalog cleaning:",
        f"  removed={cleaning['removed']}, merged={cleaning['merged']}, "
        f"normalized={cleaning['normalized']}, unverified core records={cleaning['unverified']}",
    ]
    for reason, count in sorted(cleaning["event_reasons"].items()):
        lines.append(f"  {reason}: {count}")
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
    parser.add_argument("--log", default=str(AUDIT_LOG_PATH), help="JSONL cleaning log path")
    args = parser.parse_args()
    raw_movies, prior_events = load_catalog(args.catalog)
    movies, events = clean_catalog(raw_movies)
    all_events = [*prior_events, *events]
    write_catalog_audit_log(all_events, args.log)
    report = audit(movies, all_events)
    print(format_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
