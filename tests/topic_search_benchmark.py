"""Independent direct-scan oracle for topic and description search.

Run from the repository root:
    python tests/topic_search_benchmark.py
    python tests/topic_search_benchmark.py --split tune
    python tests/topic_search_benchmark.py --split heldout

The oracle labels records by scanning catalog synopsis, keyword, theme, and
genre fields directly. It does not call the app's retrieval or ranking code.
"""

import argparse
from html import unescape
from html.parser import HTMLParser
import re
import sys
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.movies import MOVIES
from nlp.concepts import (
    MAX_EXPANDED_TERMS,
    MIN_DOCUMENT_SUPPORT,
    empty_concept_table,
    load_concept_table,
    normalize_phrase,
)


FIELDS = ("synopsis", "keywords", "themes", "genres")
RECALL_KS = (1, 5, 10, 20)
TOP_TIE_K = 5
GENERATED_SYNOPSIS_RE = re.compile(
    r"^imdb rating \d+(?:\.\d+)?/10 from [\d,]+ votes; released in \d{4}\. genres:",
    re.IGNORECASE,
)
GENERIC_TOPIC_TERMS = {
    "a", "an", "and", "about", "as", "at", "be", "but", "by", "for", "from",
    "in", "into", "is", "it", "of", "on", "or", "the", "to", "with",
    "movie", "movies", "film", "films", "something", "please", "recommend",
    "show", "watch", "ending", "end", "story",
    "one", "two", "three", "four", "five", "first", "second", "third",
    "last", "new", "old", "good", "great", "real", "life",
}


def _values(movie, field):
    value = movie.get(field, [])
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in (value or []) if isinstance(item, str) and item]


def _catalog_index(movies):
    phrases_by_movie = {}
    phrase_documents = {}
    for movie in movies:
        movie_id = str(movie["id"])
        title_values = {
            normalize_phrase(value)
            for value in (movie.get("title", ""), movie.get("original_title", ""))
            if value
        }
        fields = {}
        for field in FIELDS:
            found = set()
            for value in _values(movie, field):
                if field == "synopsis" and GENERATED_SYNOPSIS_RE.match(value.strip()):
                    continue
                normalized = normalize_phrase(value)
                if normalized in title_values:
                    continue
                tokens = normalized.split()
                for size in range(1, min(4, len(tokens)) + 1):
                    found.update(
                        " ".join(tokens[start:start + size])
                        for start in range(len(tokens) - size + 1)
                    )
            fields[field] = found
            for phrase in found:
                phrase_documents.setdefault(phrase, set()).add(movie_id)
        phrases_by_movie[movie_id] = fields
    return phrases_by_movie, phrase_documents


def _strong_evidence(movie_id, phrase, associations, phrases_by_movie):
    """Direct phrase or two distinct catalog-supported association hits."""
    fields = phrases_by_movie[movie_id]
    normalized_phrase = normalize_phrase(phrase)
    if any(normalized_phrase in values for values in fields.values()):
        return True
    hits = {
        association["term"]
        for association in associations
        if any(association["term"] in values for values in fields.values())
    }
    return len(hits) >= 2


def topic_seeds(movies, table, phrase_documents=None):
    if phrase_documents is None:
        _, phrase_documents = _catalog_index(movies)
    candidate_phrases = set()
    for movie in movies:
        title_values = {
            normalize_phrase(value)
            for value in (movie.get("title", ""), movie.get("original_title", ""))
            if value
        }
        for field in ("themes", "keywords", "genres"):
            candidate_phrases.update(
                normalize_phrase(value)
                for value in _values(movie, field)
                if normalize_phrase(value)
                and normalize_phrase(value) not in title_values
            )
    for entry in table["concepts"].values():
        candidate_phrases.update(
            normalize_phrase(alias.get("term", ""))
            for alias in entry.get("aliases", [])
            if normalize_phrase(alias.get("term", ""))
        )
    seeds = []
    for phrase in sorted(candidate_phrases):
        phrase_tokens = normalize_phrase(phrase).split()
        if (
            not phrase_tokens
            or any(token.isdigit() or token in GENERIC_TOPIC_TERMS for token in phrase_tokens)
            or any(len(token) < 3 for token in phrase_tokens)
        ):
            continue
        key = table["aliases"].get(phrase, phrase)
        entry = table["concepts"].get(key, table["concepts"].get(phrase, {}))
        associations = entry.get("associations", [])[:MAX_EXPANDED_TERMS]
        if entry.get("document_support", 0) < MIN_DOCUMENT_SUPPORT or not associations:
            continue
        support = len(phrase_documents.get(normalize_phrase(phrase), set()))
        if support < MIN_DOCUMENT_SUPPORT:
            continue
        seeds.append({
            "key": key,
            "phrase": phrase,
            "associations": associations,
            "support": support,
        })
    unique = {}
    for seed in seeds:
        unique[(seed["key"], seed["phrase"])] = seed
    return sorted(unique.values(), key=lambda seed: (seed["key"], seed["phrase"]))


def _pluralize(word):
    if word.endswith("s"):
        return word
    if word.endswith("y") and len(word) > 2:
        return word[:-1] + "ies"
    return word + "s"


def _typo(phrase):
    words = phrase.split()
    longest = max(range(len(words)), key=lambda index: (len(words[index]), words[index]))
    word = words[longest]
    if len(word) < 5:
        return phrase
    offset = len(word) // 2
    words[longest] = word[:offset] + word[offset + 1:]
    return " ".join(words)


def _query_variants(phrase):
    words = phrase.split()
    variants = [
        f"movies about {phrase}",
        f"{phrase} movies",
        f"films about {phrase}",
        f"please find movies about {phrase}",
        f"movies about {_pluralize(words[-1]) if words else phrase}",
        f"movies about {_typo(phrase)}",
    ]
    if len(words) > 1:
        variants.extend((
            f"{'-'.join(words)} movies",
            f"{''.join(words)} movies",
            f"movies about {' '.join(reversed(words))}",
        ))
    return list(dict.fromkeys(variants))


def generate_queries(movies, table):
    queries = []
    phrases_by_movie, phrase_documents = _catalog_index(movies)
    for seed in topic_seeds(movies, table, phrase_documents):
        expected = {
            movie_id
            for movie_id in phrases_by_movie
            if _strong_evidence(
                movie_id, seed["phrase"], seed["associations"], phrases_by_movie,
            )
        }
        if not expected:
            continue
        for query in _query_variants(seed["phrase"]):
            queries.append({
                "query": query,
                "topic": seed["key"],
                "phrase": seed["phrase"],
                "expected": expected,
            })
    return queries


class _Cards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.card = None
        self.capture = None
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if tag == "article" and "movie-card" in classes:
            self.card = {"id": None, "title": "", "score": None, "tier": ""}
            self.cards.append(self.card)
        elif self.card is not None and tag == "input" and attributes.get("name") == "movie_id":
            self.card["id"] = attributes.get("value")
        elif self.card is not None and tag == "h3" and "movie-card__title" in classes:
            self.capture = "title"
            self.buffer = []
        elif self.card is not None and tag == "span" and "movie-card__percent" in classes:
            self.capture = "score"
            self.buffer = []
        elif self.card is not None and tag == "span" and any(
            name.startswith("match-badge--") for name in classes
        ):
            self.capture = "tier"
            self.buffer = []

    def handle_data(self, data):
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if self.card is None or self.capture is None:
            return
        expected_tag = {"title": "h3", "score": "span", "tier": "span"}[self.capture]
        if tag != expected_tag:
            return
        value = unescape("".join(self.buffer)).strip()
        if self.capture == "score":
            match = re.search(r"(\d+)%", value)
            self.card["score"] = int(match.group(1)) if match else None
        else:
            self.card[self.capture] = value.casefold()
        self.capture = None
        self.buffer = []
        if tag == "h3" and not value:
            self.card["title"] = ""


def run_queries(queries, movies, app_module):
    from tests.test_search_golden import title_indexes

    exact_index, token_index = title_indexes(movies)
    client = app_module.app.test_client()
    results = {}
    with (
        patch("app.MOVIES", movies),
        patch("app.TITLE_INDEX", exact_index),
        patch("app.TITLE_TOKEN_INDEX", token_index),
        patch("app.allow_search", return_value=True),
    ):
        for item in queries:
            response = client.get("/", query_string={"q": item["query"], "per_page": 48})
            parser = _Cards()
            parser.feed(response.get_data(as_text=True))
            parsed = []
            for card in parser.cards:
                if card["id"] is not None:
                    parsed.append({
                        "id": str(card["id"]),
                        "score": card["score"],
                        "tier": card["tier"],
                    })
            results[(item["topic"], item["query"])] = parsed
    return results


def _split(topic):
    return "tune" if zlib.crc32(topic.encode("utf-8")) % 2 == 0 else "heldout"


def measure(queries, results):
    metrics = {
        "queries": len(queries),
        "topics": len({item["topic"] for item in queries}),
        "empty_result_rate": 0.0,
        "false_exact_rate": 0.0,
        "tie_rate": 0.0,
    }
    for k in RECALL_KS:
        metrics[f"recall@{k}"] = 0.0
        metrics[f"precision@{k}"] = 0.0
    empty_count = exact_count = false_exact = tie_pairs = all_pairs = 0
    recall_hits = {k: 0 for k in RECALL_KS}
    precision_hits = {k: 0 for k in RECALL_KS}
    query_count = len(queries) or 1
    for item in queries:
        found = results.get((item["topic"], item["query"]), [])
        expected = item["expected"]
        if not found:
            empty_count += 1
        ids = [card["id"] for card in found]
        for k in RECALL_KS:
            recall_hits[k] += len(set(ids[:k]) & expected) / max(1, len(expected))
            precision_hits[k] += (
                sum(card["id"] in expected for card in found[:k])
                / max(1, len(found[:k]))
            )
        for card in found:
            if card["tier"] == "exact match":
                exact_count += 1
                false_exact += card["id"] not in expected
        top_scores = [card["score"] for card in found[:TOP_TIE_K] if card["score"] is not None]
        for left in range(len(top_scores)):
            for right in range(left + 1, len(top_scores)):
                all_pairs += 1
                tie_pairs += top_scores[left] == top_scores[right]
    metrics["empty_result_rate"] = empty_count / query_count
    metrics["false_exact_rate"] = false_exact / exact_count if exact_count else 0.0
    metrics["tie_rate"] = tie_pairs / all_pairs if all_pairs else 0.0
    for k in RECALL_KS:
        metrics[f"recall@{k}"] = recall_hits[k] / query_count
        metrics[f"precision@{k}"] = precision_hits[k] / query_count
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("both", "tune", "heldout"), default="both")
    parser.add_argument(
        "--without-expansions", action="store_true",
        help="measure the direct lexical pipeline as the pre-expansion baseline",
    )
    args = parser.parse_args()
    import app as app_module

    table = load_concept_table(MOVIES)
    if not table["enabled"]:
        raise SystemExit("Concept table is missing or stale; run data/build_concept_expansions.py.")
    queries = generate_queries(MOVIES, table)
    if not queries:
        raise SystemExit("No topics meet the documented minimum support.")
    if args.without_expansions:
        with patch("nlp.smart_search.CONCEPT_TABLE", empty_concept_table()):
            results = run_queries(queries, MOVIES, app_module)
    else:
        results = run_queries(queries, MOVIES, app_module)
    for split in ("tune", "heldout"):
        if args.split != "both" and args.split != split:
            continue
        selected = [item for item in queries if _split(item["topic"]) == split]
        metrics = measure(selected, results)
        print(f"split={split} topics={metrics.pop('topics')} queries={metrics.pop('queries')}")
        for name, value in metrics.items():
            print(f"{name}: {value:.3f}")


if __name__ == "__main__":
    main()
