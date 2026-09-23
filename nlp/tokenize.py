"""
nlp/tokenize.py
-----------------------------------------------------------------------
STEP 3 of the pipeline: TOKENIZATION
Splits normalized text into word tokens, and exposes helpers to
generate word n-grams (bigrams/trigrams) so multi-word synonym phrases
like "edge of your seat" or "feel good" can be matched.
-----------------------------------------------------------------------
"""

STOPWORDS = {
    # English function words / filler verbs that carry no matching signal.
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with",
    "is", "it", "at", "by", "as", "be", "this", "that", "than", "then",
    "i", "you", "we", "me", "my", "your", "im", "id", "ive", "ill",
    "want", "wants", "wanting", "give", "show", "recommend", "please",
    "something", "someone", "just", "also", "can", "could", "would",
    "feel", "feeling", "like", "watching", "watch", "movie", "movies",
    "every", "all", "anything", "everything", "genre", "genres",
    "not", "no", "without", "except", "excluding", "avoid", "avoiding", "walang", "huwag", "ayaw",
    "exclude", "skip", "besides", "leave", "out", "unless", "but",
    "prefer", "preferably", "please", "rather", "mind", "too", "much", "will", "made", "make",
    "about", "movie", "movies", "film", "films", "something", "someone",
    "people", "person", "character", "characters", "story", "stories",
    "with", "from", "into", "through", "around", "someone", "something",
    "want", "watch", "watching", "recommend", "recommendation",
    "called", "named", "titled",
    "what", "whats", "really", "very", "kinda", "kind", "maybe", "pls", "lol",
    "na", "sa", "tungkol", "noong", "ba", "lang", "mas", "pero", "at", "ng",
    "film", "films", "get", "got", "have", "has", "had", "am", "are", "do",
    # Common Taglish filler particles, so keyword bags stay clean for
    # mixed English/Tagalog queries too.
    "ang", "ng", "na", "mga", "ko", "lang", "yung", "dahil", "kasi", "gusto", "din",
}


def tokenize(normalized_text):
    """Splits normalized text into a list of word tokens (the "," boundary marker is kept)."""
    if not normalized_text:
        return []
    return [t for t in normalized_text.split(" ") if t]


def remove_stopwords(tokens):
    """Used for TF-IDF and free-text keyword bags -- NOT for phrase/negation matching (which need full context)."""
    return [t for t in tokens if t not in STOPWORDS and t != ","]


def build_ngrams(tokens, max_n=3):
    """
    Builds n-grams (as space-joined strings) up to max_n words long.
    IMPORTANT: indices (start_index, end_index) refer to positions in
    the ORIGINAL `tokens` list (including "," boundary markers), so
    callers can directly compare against negation-span indices computed
    on that same list. A candidate n-gram is skipped if it would cross
    a "," clause boundary.

    Returns a list of dicts sorted longest-phrase-first per starting
    position (n descending), so a greedy longest-match strategy can be
    used by the caller.
    """
    ngrams = []
    for n in range(max_n, 0, -1):
        for i in range(0, len(tokens) - n + 1):
            slice_ = tokens[i:i + n]
            if "," in slice_:
                continue  # never span a clause boundary
            ngrams.append({
                "phrase": " ".join(slice_),
                "start_index": i,
                "end_index": i + n - 1,
                "length": n,
            })
    return ngrams
