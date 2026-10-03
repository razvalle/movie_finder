"""Diagnostic comparison against an old Render fallback snapshot only.

This output is not a correctness oracle and is not GPT-era ground truth.
Do not tune the rule-based search to reproduce its counts or result order.

Run from the repository root:
    python tests/smart_search_report.py
"""

import os
import pathlib
import re
import sys
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

BASELINE = {
    "recommend me a movie with dwarfs": ("0", []),
    "movie about a robot that falls in love": ("12", ["I, Robot", "The Wild Robot", "The Fall Guy", "Warm Bodies", "Anatomy of a Fall"]),
    "90s comedy with a road trip": ("4", ["Toy Story", "Forrest Gump", "The Truman Show", "Groundhog Day"]),
    "something like Inception but less confusing": ("2", ["Les Misérables", "Booksmart"]),
    "guy relives the same day": ("17", ["How to Lose a Guy in 10 Days", "The Fall Guy", "The Other Guys", "The Nice Guys", "Groundhog Day"]),
    "scary movie set in space": ("0", []),
    "animated movie about a rat who cooks": ("90", ["Ratatouille", "My Neighbor Totoro", "Toy Story", "Spirited Away", "Coco"]),
    "film about a chess prodigy": ("1", ["Big Hero 6"]),
    "moive with drgons": ("0", []),
}


class ResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.count = "0"
        self.titles = []
        self._capture = None
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if tag == "span" and "results__count" in classes:
            self._capture = "count"
            self._buffer = []
        elif tag == "h3" and "movie-card__title" in classes:
            self._capture = "title"
            self._buffer = []

    def handle_data(self, data):
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag):
        if self._capture == "count" and tag == "span":
            match = re.search(r"[\d,]+", "".join(self._buffer))
            if match:
                self.count = match.group(0)
            self._capture = None
        elif self._capture == "title" and tag == "h3":
            self.titles.append(unescape("".join(self._buffer)).strip())
            self._capture = None


def main():
    os.environ.pop("OPENAI_API_KEY", None)
    original_is_file = pathlib.Path.is_file
    pathlib.Path.is_file = lambda path: False if path.name == "imdb_movies.json" else original_is_file(path)
    from app import app

    client = app.test_client()
    print("Baseline count -> hybrid count | top five hybrid results")
    for query, (baseline_count, _) in BASELINE.items():
        response = client.get("/", query_string={"q": query, "per_page": 12})
        parser = ResultParser()
        parser.feed(response.get_data(as_text=True))
        print(f"{query}\t{baseline_count} -> {parser.count}\t{' | '.join(parser.titles[:5])}")


if __name__ == "__main__":
    main()