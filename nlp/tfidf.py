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

import math
import pickle
import re
from pathlib import Path
from .tokenize import remove_stopwords
from .similarity import vector_norm

WORD_RE = re.compile(r"[^a-z0-9\s-]")


def simple_word_tokens(text):
    cleaned = WORD_RE.sub(" ", text.lower())
    tokens = remove_stopwords([t for t in cleaned.split() if t])
    return [form for token in tokens for form in word_forms(token)]


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


def compute_tf(tokens):
    """Term frequency map for a token list: count / total_tokens."""
    tf = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    n = len(tokens)
    return {term: count / n for term, count in tf.items()}


def build_tfidf_model(movies):
    """
    Builds the full TF-IDF model for a movie dataset.
    Returns {"idf": {term: weight}, "vectors": {movie_id: {term: tfidf}}}
    """
    documents = [
        {"id": m["id"], "tokens": simple_word_tokens(movie_to_document_text(m))}
        for m in movies
    ]
    n_docs = len(documents)

    # Document frequency: how many documents contain each term at least once.
    df = {}
    for doc in documents:
        for term in set(doc["tokens"]):
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


def load_or_build_tfidf_model(movies, cache_path, source_path=None):
    """Load a cached model when the catalog has not changed."""
    cache_path = Path(cache_path)
    source_stamp = Path(source_path).stat().st_mtime_ns if source_path and Path(source_path).exists() else None
    fingerprint = (len(movies), source_stamp, movies[0].get("id") if movies else None, movies[-1].get("id") if movies else None)

    if cache_path.exists():
        try:
            with cache_path.open("rb") as stream:
                cached = pickle.load(stream)
            if cached.get("fingerprint") == fingerprint:
                return cached["model"]
        except (OSError, EOFError, pickle.PickleError, KeyError, ValueError):
            pass

    model = build_tfidf_model(movies)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(".tmp")
    with temporary_path.open("wb") as stream:
        pickle.dump({"fingerprint": fingerprint, "model": model}, stream, protocol=pickle.HIGHEST_PROTOCOL)
    temporary_path.replace(cache_path)
    return model


def vectorize_query(words, idf):
    """Converts an arbitrary list of free-text words into a TF-IDF vector using the model's existing IDF table."""
    tokens = [form for word in remove_stopwords([w.lower() for w in words]) for form in word_forms(word)]
    if not tokens:
        return {}
    tf = compute_tf(tokens)
    return {term: tf_value * idf[term] for term, tf_value in tf.items() if term in idf}
