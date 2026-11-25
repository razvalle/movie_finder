"""Build a reviewable, catalog-derived concept association table."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.movies import MOVIES
from data.synonyms import (
    CONTENT_DESCRIPTOR_SYNONYMS,
    GENRE_SYNONYMS,
    MOOD_SYNONYMS,
    THEME_SYNONYMS,
    build_reverse_index,
)
from nlp.concepts import (
    CONCEPT_TABLE_PATH,
    MAX_ASSOCIATIONS,
    MIN_DOCUMENT_SUPPORT,
    SCHEMA_VERSION,
    catalog_fingerprint,
    normalize_phrase,
    synonym_fingerprint,
)
from data.catalog_version import catalog_version
from nlp.smart_search import STOP_WORDS


GENERIC_TERMS = STOP_WORDS | {
    "their", "them", "they", "those", "these", "two", "one", "first",
    "second", "third", "four", "five", "six", "seven", "eight", "nine",
    "ten", "imdb", "genres", "released", "votes", "vote", "rating",
    "rated", "release", "year",
}
MAX_SINGLE_TERM_PREVALENCE = 0.35
ASSOCIATION_WEIGHT_CAP = 0.30
TOKEN_RE = re.compile(r"[a-z0-9]+")
GENERATED_SYNOPSIS_RE = re.compile(
    r"^imdb rating \d+(?:\.\d+)?/10 from [\d,]+ votes; released in \d{4}\. genres:",
    re.IGNORECASE,
)
SYNONYM_TABLES = (
    ("genre", GENRE_SYNONYMS),
    ("mood", MOOD_SYNONYMS),
    ("theme", THEME_SYNONYMS),
    ("content_descriptor", CONTENT_DESCRIPTOR_SYNONYMS),
)


def _field_values(movie, field):
    value = movie.get(field, [])
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in (value or []) if isinstance(item, str) and item]


def _tokens(text):
    return TOKEN_RE.findall(normalize_phrase(text))


def _document_terms(movie):
    terms = set()
    title_forms = {
        normalize_phrase(value)
        for value in (movie.get("title", ""), movie.get("original_title", ""))
        if value
    }
    for field in ("synopsis", "keywords", "themes", "genres"):
        for value in _field_values(movie, field):
            if field == "synopsis" and GENERATED_SYNOPSIS_RE.match(value.strip()):
                continue
            if normalize_phrase(value) in title_forms:
                continue
            tokens = _tokens(value)
            for size in range(1, min(3, len(tokens)) + 1):
                for start in range(len(tokens) - size + 1):
                    phrase = tokens[start:start + size]
                    if (
                        (size == 1 and phrase[0] in GENERIC_TERMS)
                        or phrase[0] in GENERIC_TERMS
                        or phrase[-1] in GENERIC_TERMS
                        or all(token in GENERIC_TERMS for token in phrase)
                    ):
                        continue
                    terms.add(" ".join(phrase))
    return terms


def _synonym_entries():
    entries = defaultdict(list)
    reverse = build_reverse_index()
    for category, table in SYNONYM_TABLES:
        for canonical, forms in sorted(table.items()):
            canonical_key = normalize_phrase(canonical)
            for form in sorted({canonical, *forms}):
                alias = normalize_phrase(form)
                if alias and alias not in {item["term"] for item in entries[canonical_key]}:
                    entries[canonical_key].append({
                        "term": alias,
                        "source": "hand-written",
                        "category": category,
                    })
    aliases = {}
    for alias, target in sorted(reverse.items()):
        normalized_alias = normalize_phrase(alias)
        canonical = normalize_phrase(target["tag"])
        if normalized_alias and canonical:
            aliases.setdefault(normalized_alias, canonical)
    return entries, aliases


def _associations(seed, seed_documents, term_documents, document_count):
    seed_support = len(seed_documents)
    scores = []
    seed_tokens = set(seed.split())
    for term, supporting_documents in sorted(term_documents.items()):
        term_tokens = set(term.split())
        if (
            len(term_tokens) > 3
            or len(term) < 3
            or term_tokens & seed_tokens
            or (len(term_tokens) == 1 and term in GENERIC_TERMS)
            or all(token in GENERIC_TERMS for token in term_tokens)
            or any(token.isdigit() for token in term_tokens)
            or term.isdigit()
        ):
            continue
        term_support = len(supporting_documents)
        if term_support / max(1, document_count) > MAX_SINGLE_TERM_PREVALENCE:
            continue
        cooccurrence = len(seed_documents & supporting_documents)
        if cooccurrence == 0:
            continue
        pmi = math.log2(
            (cooccurrence * document_count) / (seed_support * term_support)
        )
        if pmi <= 0:
            continue
        association_score = pmi * cooccurrence / seed_support
        scores.append((association_score, term, cooccurrence))
    scores.sort(key=lambda item: (-item[0], item[1]))
    if not scores:
        return []
    maximum = scores[0][0]
    return [
        {
            "term": term,
            "weight": round(ASSOCIATION_WEIGHT_CAP * score / maximum, 4),
            "source": "generated",
            "document_support": cooccurrence,
        }
        for score, term, cooccurrence in scores[:MAX_ASSOCIATIONS]
    ]


def build_concept_table(movies):
    term_documents = defaultdict(set)
    for index, movie in enumerate(movies):
        terms = _document_terms(movie)
        for term in terms:
            term_documents[term].add(index)

    synonym_entries, aliases = _synonym_entries()
    alias_forms = defaultdict(set)
    for alias, canonical in sorted(aliases.items()):
        alias_forms[canonical].add(alias)
    concepts = {}
    max_single_support = max(
        MIN_DOCUMENT_SUPPORT,
        math.floor(len(movies) * MAX_SINGLE_TERM_PREVALENCE),
    )
    seeds = {
        term for term, docs in term_documents.items()
        if len(docs) >= MIN_DOCUMENT_SUPPORT
        and term.split()[0] not in GENERIC_TERMS
        and term.split()[-1] not in GENERIC_TERMS
        and not all(token in GENERIC_TERMS for token in term.split())
        and (" " in term or len(docs) <= max_single_support)
    }
    seeds.update(synonym_entries)
    for seed in sorted(seeds):
        forms = sorted({seed, *alias_forms.get(seed, set())})
        supporting_documents = set().union(*(
            term_documents.get(form, set()) for form in forms
        ))
        associations = _associations(
            seed, supporting_documents, term_documents, len(movies),
        ) if len(supporting_documents) >= MIN_DOCUMENT_SUPPORT else []
        aliases_for_seed = synonym_entries.get(seed, [])
        concepts[seed] = {
            "source": (
                "generated" if len(supporting_documents) >= MIN_DOCUMENT_SUPPORT
                else "hand-written"
            ),
            "document_support": len(supporting_documents),
            "aliases": aliases_for_seed,
            "associations": associations,
        }

    return {
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "minimum_document_support": MIN_DOCUMENT_SUPPORT,
            "catalog_fingerprint": catalog_fingerprint(movies),
            "catalog_version": catalog_version(movies),
            "synonym_fingerprint": synonym_fingerprint(),
            "catalog_records": len(movies),
            "concept_count": len(concepts),
            "association_weight_cap": ASSOCIATION_WEIGHT_CAP,
        },
        "concepts": concepts,
        "aliases": aliases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=CONCEPT_TABLE_PATH,
        help="destination JSON path (default: data/concept_expansions.json)",
    )
    args = parser.parse_args()
    table = build_concept_table(MOVIES)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(table, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {table['metadata']['concept_count']} concepts from "
        f"{table['metadata']['catalog_records']} catalog records to {args.output}"
    )


if __name__ == "__main__":
    main()
