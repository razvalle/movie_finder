"""Stable content versions for catalog-derived indexes."""

import hashlib
import json


def catalog_version(movies):
    records = [
        {key: value for key, value in movie.items() if key != "poster_url"}
        for movie in movies
    ]
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return {
        "record_count": len(records),
        "content_hash": hashlib.sha256(encoded).hexdigest(),
    }
