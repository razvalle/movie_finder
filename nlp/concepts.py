"""Load catalog-derived concept expansions when their source data is current."""

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

from data.catalog_version import catalog_version
from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)


SCHEMA_VERSION = 2
MIN_DOCUMENT_SUPPORT = 2
MAX_ASSOCIATIONS = 64
MAX_EXPANSION_WEIGHT = 0.30
MAX_EXPANDED_TERMS = 24
CONTENT_FIELDS = ("synopsis", "keywords", "themes", "genres")
CONCEPT_TABLE_PATH = Path(__file__).resolve().parents[1] / "data" / "concept_expansions.json"
_LOGGER = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_SYNONYM_TABLES = (
    ("genre", GENRE_SYNONYMS),
    ("mood", MOOD_SYNONYMS),
    ("theme", THEME_SYNONYMS),
    ("content_descriptor", CONTENT_DESCRIPTOR_SYNONYMS),
)


def normalize_phrase(value):
    normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return " ".join(_TOKEN_RE.findall(normalized.casefold()))


def _catalog_payload(movies):
    rows = []
    for movie in movies:
        rows.append({
            "id": str(movie.get("id", "")),
            "title": normalize_phrase(movie.get("title", "")),
            "original_title": normalize_phrase(movie.get("original_title", "")),
            **{
                field: sorted({
                    normalize_phrase(value)
                    for value in (
                        [movie.get(field, "")]
                        if isinstance(movie.get(field), str)
                        else movie.get(field, []) or []
                    )
                    if normalize_phrase(value)
                })
                for field in CONTENT_FIELDS
            },
        })
    return rows


def catalog_fingerprint(movies):
    encoded = json.dumps(
        _catalog_payload(movies), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def synonym_payload():
    return {
        category: {
            normalize_phrase(canonical): sorted({
                normalize_phrase(form)
                for form in [canonical, *forms]
                if normalize_phrase(form)
            })
            for canonical, forms in sorted(table.items())
        }
        for category, table in _SYNONYM_TABLES
    }


def synonym_fingerprint():
    encoded = json.dumps(
        synonym_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def empty_concept_table(status="unavailable"):
    return {"enabled": False, "concepts": {}, "aliases": {}, "status": status}


def _valid_concept_rows(concepts):
    for key, entry in concepts.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("document_support"), int):
            return False
        if not isinstance(entry.get("aliases"), list) or not isinstance(entry.get("associations"), list):
            return False
        for alias in entry["aliases"]:
            if not isinstance(alias, dict) or not isinstance(alias.get("term"), str):
                return False
            if alias.get("source") != "hand-written":
                return False
        for association in entry["associations"]:
            if (
                not isinstance(association, dict)
                or not isinstance(association.get("term"), str)
                or association.get("source") != "generated"
                or not isinstance(association.get("weight"), (int, float))
                or not 0 < association["weight"] <= MAX_EXPANSION_WEIGHT
            ):
                return False
    return True


def load_concept_table(movies, path=CONCEPT_TABLE_PATH):
    """Return no expansions when the generated table is missing or stale."""
    version = catalog_version(movies)
    try:
        table = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _LOGGER.info("Concept-expansion table is missing; using lexical search only.")
        return empty_concept_table("missing")
    except (OSError, json.JSONDecodeError) as error:
        _LOGGER.warning("Concept-expansion table cannot be read; using lexical search only: %s", error)
        return empty_concept_table("invalid")

    metadata = table.get("metadata") if isinstance(table, dict) else None
    current = isinstance(metadata, dict) and (
        metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("catalog_fingerprint") == catalog_fingerprint(movies)
        and metadata.get("catalog_version") == version
        and metadata.get("catalog_records") == version["record_count"]
        and metadata.get("synonym_fingerprint") == synonym_fingerprint()
    )
    concepts = table.get("concepts") if isinstance(table, dict) else None
    aliases = table.get("aliases") if isinstance(table, dict) else None
    if (
        not current
        or not isinstance(concepts, dict)
        or not isinstance(aliases, dict)
        or not _valid_concept_rows(concepts)
        or not all(
            isinstance(alias, str) and isinstance(key, str) and key in concepts
            for alias, key in aliases.items()
        )
    ):
        _LOGGER.warning("Concept-expansion table is stale or invalid; using lexical search only.")
        return empty_concept_table("stale")
    return {
        "enabled": True,
        "concepts": concepts,
        "aliases": {
            normalize_phrase(alias): key
            for alias, key in aliases.items()
            if isinstance(alias, str) and isinstance(key, str) and key in concepts
        },
        "catalog_version": version,
        "status": "ready",
    }


def load_or_rebuild_concept_table(
    movies,
    path=CONCEPT_TABLE_PATH,
    max_rebuild_records=5000,
    max_rebuild_seconds=3.0,
):
    table = load_concept_table(movies, path)
    if table["enabled"]:
        return table
    if len(movies) > max_rebuild_records:
        _LOGGER.error(
            "Concept table is %s for %s records; local rebuild limit is %s.",
            table["status"],
            len(movies),
            max_rebuild_records,
        )
        return table

    from data.build_concept_expansions import build_concept_table

    started = time.perf_counter()
    try:
        generated = build_concept_table(movies)
    except (ImportError, KeyError, TypeError, ValueError, ArithmeticError) as error:
        _LOGGER.exception("Concept-table rebuild failed; using lexical search: %s", error)
        return table
    elapsed = time.perf_counter() - started
    if elapsed > max_rebuild_seconds:
        _LOGGER.error(
            "Concept-table rebuild took %.3fs, over the %.1fs startup budget; using lexical search.",
            elapsed,
            max_rebuild_seconds,
        )
        return table

    destination = Path(path)
    temporary_path = destination.with_name(f"{destination.name}.{os.getpid()}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(generated, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(destination)
    except OSError as error:
        _LOGGER.error("Rebuilt concept table could not be saved; using lexical search: %s", error)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            _LOGGER.warning("Incomplete concept table could not be removed: %s", cleanup_error)
        return table

    rebuilt = load_concept_table(movies, path)
    if not rebuilt["enabled"]:
        _LOGGER.error("Rebuilt concept table failed validation; using lexical search.")
    return rebuilt


def expand_query(concepts, understanding, table, excluded_terms=()):
    """Resolve seeds to bounded, catalog-generated associations."""
    if not table.get("enabled"):
        return []
    excluded_phrases = {normalize_phrase(term) for term in excluded_terms}
    excluded = {
        token for phrase in excluded_phrases for token in phrase.split()
    }
    seed_phrases = list(dict.fromkeys(
        normalize_phrase(" ".join(concept))
        for concept in concepts if normalize_phrase(" ".join(concept))
    ))
    for preference in understanding.get("soft_preferences", []):
        seed = normalize_phrase(preference.get("value", "") if isinstance(preference, dict) else preference)
        key = table["aliases"].get(seed, seed)
        entry = table["concepts"].get(key, {})
        if any(alias.get("category") == "theme" for alias in entry.get("aliases", [])):
            seed_phrases.append(seed)
    seed_phrases = list(dict.fromkeys(seed_phrases))

    direct_terms = {
        token for seed in seed_phrases for token in seed.split()
    }
    expanded = {}
    for seed in seed_phrases:
        key = table["aliases"].get(seed, seed)
        entry = table["concepts"].get(key, {})
        for association in entry.get("associations", []):
            term = normalize_phrase(association.get("term", ""))
            weight = association.get("weight", 0)
            tokens = set(term.split())
            if (
                not term
                or term in direct_terms
                or tokens & excluded
                or term in excluded_phrases
                or not isinstance(weight, (int, float))
                or weight <= 0
            ):
                continue
            previous = expanded.get(term)
            if previous is None or weight > previous["weight"]:
                expanded[term] = {
                    "weight": min(float(weight), MAX_EXPANSION_WEIGHT),
                    "concept": seed,
                }
    selected = sorted(
        expanded.items(), key=lambda item: (-item[1]["weight"], item[0])
    )[:MAX_EXPANDED_TERMS]
    total_weight = sum(item["weight"] for _, item in selected)
    scale = min(1.0, MAX_EXPANSION_WEIGHT / total_weight) if total_weight else 0.0
    return [
        {
            "term": term,
            "weight": round(item["weight"] * scale, 6),
            "association_weight": min(float(item["weight"]), MAX_EXPANSION_WEIGHT),
            "source": "generated",
            "concept": item["concept"],
        }
        for term, item in selected
    ]
