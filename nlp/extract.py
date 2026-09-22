"""
nlp/extract.py
-----------------------------------------------------------------------
STEPS 4-9 of the pipeline, combined: PREFERENCE EXTRACTION

Orchestrates the smaller single-purpose modules (normalize, tokenize,
negation, runtime, release_year) and the synonym dictionary to turn raw
user text into a structured preference dict:

{
  "raw_input": str, "normalized_text": str, "tokens": [...],
  "moods": [...],            "genres": [...],            "themes": [...],
  "excluded_moods": [...],   "excluded_genres": [...],    "excluded_themes": [...],
  "runtime": {"min", "max", "target", "tolerance"},
  "release_year": {"min", "max"} | None,
  "free_text_keywords": [...],   # leftover meaningful words for TF-IDF fallback
  "negation_scopes": [(int, int), ...]  # kept for debugging/tests
}
-----------------------------------------------------------------------
"""

from difflib import SequenceMatcher

from .normalize import normalize_text
from .tokenize import tokenize, build_ngrams, remove_stopwords
from .negation import find_negation_scopes, is_retroactively_negated, is_within_negation_scope
from .runtime import extract_runtime_constraint
from .release_year import extract_release_year_constraint
from data.synonyms import build_reverse_index

REVERSE_INDEX = build_reverse_index()  # built once at module import


def fuzzy_synonym_hit(phrase):
    """Recover a likely dictionary word from a short user typo.

    Fuzzy matching is limited to single words and close spellings so it
    cannot turn ordinary descriptive prose into an unrelated preference.
    """
    if " " in phrase or len(phrase) < 4:
        return None
    best_key = None
    best_ratio = 0.0
    for key in REVERSE_INDEX:
        if " " in key or abs(len(key) - len(phrase)) > 2:
            continue
        ratio = SequenceMatcher(None, phrase, key).ratio()
        if ratio > best_ratio:
            best_key, best_ratio = key, ratio
    if best_ratio >= 0.82:
        return REVERSE_INDEX[best_key]
    return None


def extract_preferences(raw_input):
    normalized_text = normalize_text(raw_input)
    tokens = tokenize(normalized_text)
    negation_scopes = find_negation_scopes(tokens)

    moods, genres, themes = set(), set(), set()
    excluded_moods, excluded_genres, excluded_themes = set(), set(), set()

    # Track which token indices got claimed by a category match, so we
    # can build a "leftover words" bag for the TF-IDF/synopsis-similarity
    # fallback (this is what lets free descriptive text like "heist" or
    # "small town" still influence scoring even without an exact dict hit).
    claimed_indices = set()

    ngrams = build_ngrams(tokens, 3)
    # build_ngrams already yields longest-phrase-first per position, so
    # the first successful lookup at a given index wins (greedy longest
    # match).
    for ngram in ngrams:
        start, end = ngram["start_index"], ngram["end_index"]
        if any(k in claimed_indices for k in range(start, end + 1)):
            continue  # already claimed by a longer phrase

        hit = REVERSE_INDEX.get(ngram["phrase"])
        if not hit:
            hit = fuzzy_synonym_hit(ngram["phrase"])
        if not hit:
            continue

        negated = is_within_negation_scope(start, end, negation_scopes)
        if not negated:
            negated = is_retroactively_negated(end, tokens)
        target_set = {
            "mood": excluded_moods if negated else moods,
            "genre": excluded_genres if negated else genres,
            "theme": excluded_themes if negated else themes,
        }[hit["category"]]

        target_set.add(hit["tag"])
        for k in range(start, end + 1):
            claimed_indices.add(k)

    runtime = extract_runtime_constraint(normalized_text)
    release_year = extract_release_year_constraint(normalized_text)

    # Leftover words feed the TF-IDF/cosine-similarity fallback so
    # descriptive free text not covered by the synonym dictionary still
    # contributes to matching (e.g. "hotel", "detective", "wedding").
    leftover_tokens = [t for i, t in enumerate(tokens) if i not in claimed_indices and t != ","]
    free_text_keywords = remove_stopwords(leftover_tokens)

    return {
        "raw_input": raw_input,
        "normalized_text": normalized_text,
        "tokens": tokens,
        "moods": sorted(moods),
        "genres": sorted(genres),
        "themes": sorted(themes),
        "excluded_moods": sorted(excluded_moods),
        "excluded_genres": sorted(excluded_genres),
        "excluded_themes": sorted(excluded_themes),
        "runtime": runtime,
        "release_year": release_year,
        "free_text_keywords": free_text_keywords,
        "negation_scopes": negation_scopes,
    }
