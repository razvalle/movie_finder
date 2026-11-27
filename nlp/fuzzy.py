"""Conservative token correction for catalog-backed search."""

import re
from functools import lru_cache


@lru_cache(maxsize=64)
def _vocabulary_by_length(vocabulary):
    buckets = {}
    for candidate in vocabulary:
        buckets.setdefault(len(candidate), set()).add(candidate)
    return buckets


def _edit_distance(left, right, limit):
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_minimum = left_index
        for right_index, right_char in enumerate(right, start=1):
            current_value = min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            )
            current.append(current_value)
            row_minimum = min(row_minimum, current_value)
        if row_minimum > limit:
            return limit + 1
        previous = current
    return previous[-1]


def correct_unknown_token(
    token,
    vocabulary,
    document_frequency=None,
    protected_tokens=(),
    known_tokens=(),
):
    """Return a unique, close catalog token for an unknown query token."""
    normalized = str(token).casefold()
    if (
        isinstance(vocabulary, (set, frozenset, dict))
        and normalized in vocabulary
    ) or normalized in known_tokens:
        return None
    vocabulary = {str(candidate).casefold() for candidate in vocabulary}
    protected = {str(value).casefold() for value in protected_tokens}
    if (
        not normalized
        or normalized in vocabulary
        or normalized in protected
        or re.fullmatch(r"\d+(?:\.\d+)?|\d{2,4}s?", normalized)
    ):
        return None

    max_distance = 1 if len(normalized) <= 5 else 2
    from .tfidf import word_forms

    inflectional_forms = word_forms(normalized) - {normalized}
    if inflectional_forms & (vocabulary | protected | set(known_tokens)):
        return None
    distances = []
    length_buckets = _vocabulary_by_length(tuple(sorted(vocabulary)))
    candidate_buckets = (
        candidate
        for length in range(
            max(0, len(normalized) - max_distance),
            len(normalized) + max_distance + 1,
        )
        for candidate in length_buckets.get(length, ())
    )
    for candidate in candidate_buckets:
        if (
            candidate in protected
            or re.fullmatch(r"\d+(?:\.\d+)?|\d{2,4}s?", candidate)
            or abs(len(normalized) - len(candidate)) > max_distance
            or (
                len(candidate) < len(normalized)
                and candidate in normalized
                and candidate not in inflectional_forms
            )
        ):
            continue
        distance = _edit_distance(normalized, candidate, max_distance)
        if distance <= max_distance:
            distances.append((distance, candidate))
    if not distances:
        return None

    best_distance = min(distance for distance, _ in distances)
    nearest = [
        candidate for distance, candidate in distances
        if distance == best_distance
    ]
    if len(nearest) > 1:
        frequencies = document_frequency or {}
        highest_frequency = max(
            frequencies.get(candidate, 0) for candidate in nearest
        )
        nearest = [
            candidate for candidate in nearest
            if frequencies.get(candidate, 0) == highest_frequency
        ]
    return nearest[0] if len(nearest) == 1 else None
