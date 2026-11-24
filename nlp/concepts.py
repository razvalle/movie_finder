"""Load catalog-derived concept expansions when their source data is current."""

import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path

from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
)


SCHEMA_VERSION = 1
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


def empty_concept_table():
    return {"enabled": False, "concepts": {}, "aliases": {}}


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
    try:
        table = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _LOGGER.info("Concept-expansion table is missing; using lexical search only.")
        return empty_concept_table()
    except (OSError, json.JSONDecodeError) as error:
        _LOGGER.warning("Concept-expansion table cannot be read; using lexical search only: %s", error)
        return empty_concept_table()

    metadata = table.get("metadata") if isinstance(table, dict) else None
    current = isinstance(metadata, dict) and (
        metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("catalog_fingerprint") == catalog_fingerprint(movies)
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
        return empty_concept_table()
    return {
        "enabled": True,
        "concepts": concepts,
        "aliases": {
            normalize_phrase(alias): key
            for alias, key in aliases.items()
            if isinstance(alias, str) and isinstance(key, str) and key in concepts
        },
    }


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
