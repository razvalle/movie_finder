"""
nlp/tfidf.py
-----------------------------------------------------------------------
STEP 11 of the pipeline: TF-IDF (explainable text weighting)

Builds a TF-IDF model over the movie corpus (title + synopsis + keywords +
themes + mood_tags joined per movie = one "document" per movie), so
free-text words the user types that AREN'T in the synonym dictionary
(e.g. "hotel", "wedding", "detective") can still contribute to
similarity scoring via cosine similarity (see similarity.py).

This is a from-scratch, fully inspectable implementation -- no
pretrained model or embedding is used anywhere in this file.
-----------------------------------------------------------------------
"""

import logging
import math
import os
import pickle
import re
import time
from pathlib import Path
from data.catalog_version import catalog_version
from .tokenize import remove_stopwords
from .similarity import vector_norm

WORD_RE = re.compile(r"[^a-z0-9\s-]")
TOKENIZER_VERSION = 2
_LOGGER = logging.getLogger(__name__)
MAX_LOCAL_REBUILD_RECORDS = 5000
MAX_LOCAL_REBUILD_SECONDS = 3.0


def simple_word_tokens(text):
    cleaned = WORD_RE.sub(" ", text.lower())
    tokens = remove_stopwords([t for t in cleaned.split() if t])
    expanded = []
    for token in tokens:
        expanded.extend(word_forms(token))
        if "-" in token:
            for component in remove_stopwords(token.split("-")):
                expanded.extend(word_forms(component))
    return expanded


def word_forms(token):
    """Return a word plus conservative singular and verb variants."""
    forms = {token}
    if len(token) > 4 and token.endswith("ies"):
        forms.add(token[:-3] + "y")
    elif len(token) > 4 and token.endswith("es"):
        forms.add(token[:-2])
    elif len(token) > 3 and token.endswith("s"):
        forms.add(token[:-1])
    if len(token) > 5 and token.endswith("ing"):
        stem = token[:-3]
        forms.add(stem)
        if len(stem) > 2 and stem[-1] == stem[-2]:
            forms.add(stem[:-1])
    if len(token) > 4 and token.endswith("ed"):
        forms.add(token[:-2])
    return forms


def movie_to_document_text(movie):
    return " ".join([
        movie["title"],
        movie["synopsis"],
        " ".join(movie["genres"]),
        " ".join(movie["themes"]),
        " ".join(movie["mood_tags"]),
        " ".join(movie["keywords"]),
    ])


def movie_to_similarity_document_text(movie):
    """Fields used for title-to-title similarity, excluding names and mood labels."""
    values = [
        movie.get("synopsis", ""),
        movie.get("overview", ""),
        *(movie.get("genres") or []),
        *(movie.get("themes") or []),
        *(movie.get("keywords") or []),
    ]
    return " ".join(str(value) for value in values if value)


def compute_tf(tokens):
    """Term frequency map for a token list: count / total_tokens."""
    tf = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    n = len(tokens)
    return {term: count / n for term, count in tf.items()}


def build_tfidf_model(movies, document_text_builder=movie_to_document_text):
    """
    Builds the full TF-IDF model for a movie dataset.
    Returns {"idf": {term: weight}, "vectors": {movie_id: {term: tfidf}}}
    """
    documents = [
        {"id": m["id"], "tokens": simple_word_tokens(document_text_builder(m))}
        for m in movies
    ]
    n_docs = len(documents)

    # Document frequency: how many documents contain each term at least once.
    df = {}
    for doc in documents:
        for term in sorted(set(doc["tokens"])):
            df[term] = df.get(term, 0) + 1

    # Smoothed IDF so a term appearing in every document doesn't hit zero.
    idf = {term: math.log((1 + n_docs) / (1 + count)) + 1 for term, count in df.items()}

    vectors = {}
    norms = {}
    for doc in documents:
        tf = compute_tf(doc["tokens"])
        vectors[doc["id"]] = {term: tf_value * idf.get(term, 0) for term, tf_value in tf.items()}
        norms[doc["id"]] = vector_norm(vectors[doc["id"]])

    return {"idf": idf, "vectors": vectors, "norms": norms}


def load_or_build_tfidf_model(
    movies,
    cache_path,
    source_path=None,
    document_text_builder=movie_to_document_text,
):
    """Load a cached model when the catalog has not changed."""
    cache_path = Path(cache_path)
    source_stamp = Path(source_path).stat().st_mtime_ns if source_path and Path(source_path).exists() else None
    version = catalog_version(movies)
    fingerprint = (
        TOKENIZER_VERSION,
        document_text_builder.__module__,
        document_text_builder.__qualname__,
        source_stamp,
        version["record_count"],
        version["content_hash"],
    )

    if cache_path.exists():
        try:
            with cache_path.open("rb") as stream:
                cached = pickle.load(stream)
            if (
                isinstance(cached, dict)
                and cached.get("fingerprint") == fingerprint
                and cached.get("catalog_version") == version
                and cached.get("index_count") == version["record_count"]
            ):
                cached_model = cached.get("model")
                if (
                    isinstance(cached_model, dict)
                    and isinstance(cached_model.get("idf"), dict)
                    and isinstance(cached_model.get("vectors"), dict)
                    and isinstance(cached_model.get("norms"), dict)
                    and len(cached_model["vectors"]) == version["record_count"]
                    and cached_model.get("_catalog_version") == version
                    and cached_model.get("_index_count") == version["record_count"]
                ):
                    return cached_model
                _LOGGER.warning("TF-IDF cache has an invalid model shape; rebuilding it locally.")
            else:
                _LOGGER.info("TF-IDF cache is stale; rebuilding it locally.")
        except (OSError, EOFError, pickle.PickleError, KeyError, ValueError) as error:
            _LOGGER.warning("TF-IDF cache cannot be loaded; rebuilding it locally: %s", error)

    if len(movies) > MAX_LOCAL_REBUILD_RECORDS:
        _LOGGER.error(
            "TF-IDF index is stale or missing for %s records; local rebuild limit is %s.",
            len(movies),
            MAX_LOCAL_REBUILD_RECORDS,
        )
        return None

    started = time.perf_counter()
    model = build_tfidf_model(movies, document_text_builder=document_text_builder)
    elapsed = time.perf_counter() - started
    if elapsed > MAX_LOCAL_REBUILD_SECONDS:
        _LOGGER.error(
            "TF-IDF rebuild took %.3fs, over the %.1fs startup budget; using keyword fallback.",
            elapsed,
            MAX_LOCAL_REBUILD_SECONDS,
        )
        return None

    model["_catalog_version"] = version
    model["_index_count"] = len(model["vectors"])
    temporary_path = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("wb") as stream:
            pickle.dump({
                "fingerprint": fingerprint,
                "catalog_version": version,
                "index_count": len(model["vectors"]),
                "model": model,
            }, stream, protocol=pickle.HIGHEST_PROTOCOL)
        temporary_path.replace(cache_path)
    except OSError as error:
        _LOGGER.warning("TF-IDF cache could not be saved; using the in-memory model: %s", error)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            _LOGGER.warning("Could not remove the incomplete TF-IDF cache file.")
    return model


def vectorize_query(words, idf):
    """Converts an arbitrary list of free-text words into a TF-IDF vector using the model's existing IDF table."""
    tokens = [form for word in remove_stopwords([w.lower() for w in words]) for form in word_forms(word)]
    if not tokens:
        return {}
    tf = compute_tf(tokens)
    return {term: tf_value * idf[term] for term, tf_value in tf.items() if term in idf}
