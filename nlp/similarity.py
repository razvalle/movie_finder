"""
nlp/similarity.py
-----------------------------------------------------------------------
STEP 12 of the pipeline: COSINE SIMILARITY
Generic sparse-vector cosine similarity, used to compare the user's
free-text TF-IDF vector against each movie's TF-IDF document vector.
Vectors are dict[term, weight] (sparse -- only non-zero terms stored).
-----------------------------------------------------------------------
"""

import math


def vector_norm(vector):
    """Return a sparse vector's Euclidean norm."""
    return math.sqrt(sum(weight * weight for weight in vector.values()))


def cosine_similarity(vec_a, vec_b, norm_a=None, norm_b=None):
    if not vec_a or not vec_b:
        return 0.0

    # Iterate the smaller dict for efficiency.
    small, large = (vec_a, vec_b) if len(vec_a) < len(vec_b) else (vec_b, vec_a)

    dot = sum(weight * large[term] for term, weight in small.items() if term in large)

    mag_a = norm_a if norm_a is not None else vector_norm(vec_a)
    mag_b = norm_b if norm_b is not None else vector_norm(vec_b)
    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)
