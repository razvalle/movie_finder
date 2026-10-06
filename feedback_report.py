"""Offline feedback report for a human to read when tuning scoring weights by hand.

This is read-only analysis. Nothing here changes weights or ranking automatically.

Run from the repository root:
    python feedback_report.py [--db path/to/feedback.sqlite3] [--min-votes 3]
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from feedback_store import FEEDBACK_DB_PATH


def load_votes(db_path):
    if not Path(db_path).is_file():
        return []
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(
            "SELECT query, movie_id, movie_title, relevant FROM result_feedback"
        ).fetchall()


def summarize(votes, min_votes=3):
    by_query = defaultdict(lambda: [0, 0])
    by_movie = defaultdict(lambda: [0, 0, ""])
    for query, movie_id, title, relevant in votes:
        by_query[query.strip().casefold()][0 if relevant else 1] += 1
        entry = by_movie[movie_id]
        entry[0 if relevant else 1] += 1
        entry[2] = title
    queries = sorted(
        ((q, up, down) for q, (up, down) in by_query.items() if up + down >= 1),
        key=lambda row: (row[1] / (row[1] + row[2]), -(row[1] + row[2])),
    )
    movies = sorted(
        ((title, up, down) for up, down, title in by_movie.values() if up + down >= min_votes),
        key=lambda row: (row[1] / (row[1] + row[2]), -(row[1] + row[2])),
    )
    return {
        "total": len(votes),
        "relevant_rate": sum(1 for v in votes if v[3]) / len(votes) if votes else 0.0,
        "queries": queries,
        "movies": movies,
    }


def format_report(summary, limit=15):
    lines = [f"Votes: {summary['total']}  relevant rate: {summary['relevant_rate']:.1%}", "",
             "Queries with the lowest relevant rate (review parsing/scoring for these):"]
    for query, up, down in summary["queries"][:limit]:
        lines.append(f"  {up}/{up + down} relevant  {query}")
    lines += ["", "Movies most often marked not relevant:"]
    for title, up, down in summary["movies"][:limit]:
        lines.append(f"  {up}/{up + down} relevant  {title}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(FEEDBACK_DB_PATH))
    parser.add_argument("--min-votes", type=int, default=3)
    args = parser.parse_args()
    votes = load_votes(args.db)
    if not votes:
        print(f"No feedback found in {args.db}.")
        return 0
    print(format_report(summarize(votes, args.min_votes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
