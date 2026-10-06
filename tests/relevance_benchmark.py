"""Relevance benchmark: queries are generated from catalog fields, not hand-written.

Each query's source movie is its known-relevant answer. Movies are split
deterministically into a TUNE set (the only set allowed for weight tuning) and
a HELDOUT set (reporting only; never tune against it).

Run from the repository root:
    python tests/relevance_benchmark.py                 # heldout (default)
    python tests/relevance_benchmark.py --split tune
"""

import argparse
import random
import re
import sys
import zlib
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEED = 20240601
QUERIES_PER_SPLIT = 100
RECALL_KS = (1, 5, 10, 20)
GENRE_WORDS = {"sci-fi": "science fiction"}

TEMPLATES = [
    ("{genre} movie from the {decade}s", ("genre", "decade")),
    ("{decade}s {genre} film under {max_runtime} minutes", ("genre", "decade", "runtime")),
    ("{genre} movie about {keyword}", ("genre", "keyword")),
    ("{genre} {keyword} film from the {decade}s", ("genre", "keyword", "decade")),
    ("{genre} and {genre2} movies from the {decade}s", ("genre", "genre2", "decade")),
    ("a {genre} movie shorter than {max_runtime} minutes with {keyword}", ("genre", "runtime", "keyword")),
]


def split_of(movie):
    """Stable assignment so a record never moves between tune and heldout."""
    return "heldout" if zlib.crc32(str(movie["id"]).encode()) % 2 == 0 else "tune"


def _keyword(movie):
    genres = {g.casefold() for g in movie["genres"]}
    for word in re.findall(r"[a-z]{4,}", movie["title"].casefold()):
        if word not in genres:
            return word
    return None


def _usable(movie):
    return bool(movie.get("genres") and movie.get("release_year") and movie.get("runtime"))


def generate_queries(movies, split, count=QUERIES_PER_SPLIT, seed=SEED):
    rng = random.Random(f"{seed}:{split}")
    pool = [m for m in movies if _usable(m) and split_of(m) == split]
    rng.shuffle(pool)
    queries = []
    for movie in pool:
        if len(queries) >= count:
            break
        template, needs = rng.choice(TEMPLATES)
        keyword = _keyword(movie)
        if ("keyword" in needs and not keyword) or ("genre2" in needs and len(movie["genres"]) < 2):
            continue
        genres = movie["genres"][:2] if "genre2" in needs else movie["genres"][:1]
        decade = movie["release_year"] // 10 * 10
        max_runtime = (movie["runtime"] // 10 + 1) * 10 + 10
        text = template.format(
            genre=GENRE_WORDS.get(genres[0], genres[0]),
            genre2=GENRE_WORDS.get(genres[-1], genres[-1]),
            decade=decade, max_runtime=max_runtime, keyword=keyword,
        )
        constraints = {"genres": genres}
        if "decade" in needs:
            constraints["years"] = (decade, decade + 9)
        if "runtime" in needs:
            constraints["max_runtime"] = max_runtime
        if "keyword" in needs:
            constraints["keyword"] = keyword
        queries.append({"query": text, "source_id": movie["id"], "source_title": movie["title"],
                        "constraints": constraints})
    return queries


def violated_kinds(movie, constraints):
    kinds = []
    if any(g not in movie["genres"] for g in constraints["genres"]):
        kinds.append("genre")
    if "years" in constraints and not constraints["years"][0] <= movie["release_year"] <= constraints["years"][1]:
        kinds.append("decade")
    if "max_runtime" in constraints and (movie.get("runtime") or 0) > constraints["max_runtime"]:
        kinds.append("runtime")
    if "keyword" in constraints:
        text = " ".join([movie["title"], movie.get("synopsis", ""), *movie.get("keywords", [])]).casefold()
        if constraints["keyword"] not in text:
            kinds.append("keyword")
    return kinds


def violates(movie, constraints):
    return bool(violated_kinds(movie, constraints))


def score(queries, results_by_query, by_id):
    """Results may be ids or (id, unverified) pairs; report both certainty levels."""
    recalls = {k: 0 for k in RECALL_KS}
    violating = returned = empty = 0
    by_kind = {}
    verified_returned = verified_violating = unverified_returned = unverified_violating = 0
    for item in queries:
        results = results_by_query[item["query"]]
        if not results:
            empty += 1
        ids = [result[0] if isinstance(result, tuple) else result for result in results]
        for k in RECALL_KS:
            recalls[k] += item["source_id"] in ids[:k]
        for result in results:
            movie_id, is_unverified = result if isinstance(result, tuple) else (result, False)
            returned += 1
            kinds = violated_kinds(by_id[movie_id], item["constraints"])
            violating += bool(kinds)
            if is_unverified:
                unverified_returned += 1
                unverified_violating += bool(kinds)
            else:
                verified_returned += 1
                verified_violating += bool(kinds)
            for kind in kinds:
                by_kind[kind] = by_kind.get(kind, 0) + 1
    total = len(queries) or 1
    return {
        "queries": len(queries),
        **{f"recall@{k}": recalls[k] / total for k in RECALL_KS},
        "hard_constraint_violation_rate": violating / returned if returned else 0.0,
        "verified_hard_constraint_violation_rate": verified_violating / verified_returned if verified_returned else 0.0,
        "unverified_result_rate": unverified_returned / returned if returned else 0.0,
        "unverified_hard_constraint_violation_rate": unverified_violating / unverified_returned if unverified_returned else 0.0,
        **{f"violation_rate[{kind}]": count / returned for kind, count in sorted(by_kind.items())},
        "empty_result_rate": empty / total,
    }


class _ResultCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.card = None
        self.capture_title = False
        self.title_buffer = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if tag == "article" and "movie-card" in classes:
            self.card = {"title": "", "unverified": False}
            self.cards.append(self.card)
        elif self.card is not None and tag == "span" and "match-badge--unverified" in classes:
            self.card["unverified"] = True
        elif self.card is not None and tag == "h3" and "movie-card__title" in classes:
            self.capture_title = True
            self.title_buffer = []

    def handle_data(self, data):
        if self.capture_title:
            self.title_buffer.append(data)

    def handle_endtag(self, tag):
        if self.capture_title and tag == "h3":
            self.card["title"] = "".join(self.title_buffer).strip()
            self.capture_title = False
        elif self.card is not None and tag == "article":
            self.card = None


def run_search(queries, movies):
    import app as app_module
    from tests.test_search_golden import title_indexes

    exact_index, token_index = title_indexes(movies)
    by_title = {}
    for movie in movies:
        by_title.setdefault(movie["title"], []).append(movie["id"])
    client = app_module.app.test_client()
    out = {}
    with (
        patch("app.MOVIES", movies), patch("app.TITLE_INDEX", exact_index),
        patch("app.TITLE_TOKEN_INDEX", token_index), patch("app.allow_search", return_value=True),
    ):
        for item in queries:
            html = client.get("/", query_string={"q": item["query"], "per_page": 48}).data
            parser = _ResultCards()
            parser.feed(html.decode("utf-8"))
            ids = []
            for result in parser.cards:
                candidates = by_title.get(result["title"], [])
                # Duplicate titles: credit the source record if that title is returned.
                movie_id = item["source_id"] if item["source_id"] in candidates else (candidates[0] if candidates else None)
                if movie_id is not None:
                    ids.append((movie_id, result["unverified"]))
            out[item["query"]] = ids
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("tune", "heldout"), default="heldout")
    args = parser.parse_args()
    from tests.test_search_golden import deployment_catalog

    movies = deployment_catalog()
    by_id = {m["id"]: m for m in movies}
    queries = generate_queries(movies, args.split)
    metrics = score(queries, run_search(queries, movies), by_id)
    print(f"split={args.split}" + ("  (reporting only; do not tune on this set)" if args.split == "heldout" else ""))
    for name, value in metrics.items():
        print(f"{name}: {value:.3f}" if isinstance(value, float) else f"{name}: {value}")


if __name__ == "__main__":
    main()
