import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from poster_storage import insert_feedback, supabase_is_configured


FEEDBACK_DB_PATH = Path(os.environ.get(
    "MOVIE_FINDER_FEEDBACK_DB",
    Path(__file__).parent / "instance" / "feedback.sqlite3",
))


def record_feedback(query, movie_id, movie_title, relevant, db_path=None):
    if supabase_is_configured():
        insert_feedback({
            "query": query,
            "movie_id": movie_id,
            "movie_title": movie_title,
            "relevant": bool(relevant),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return

    path = Path(db_path or FEEDBACK_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS result_feedback (
                    id INTEGER PRIMARY KEY,
                    query TEXT NOT NULL,
                    movie_id INTEGER NOT NULL,
                    movie_title TEXT NOT NULL,
                    relevant INTEGER NOT NULL CHECK (relevant IN (0, 1)),
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """INSERT INTO result_feedback
                    (query, movie_id, movie_title, relevant, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                (
                    query,
                    movie_id,
                    movie_title,
                    int(relevant),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )