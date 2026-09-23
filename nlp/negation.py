"""
nlp/negation.py
-----------------------------------------------------------------------
STEP 10 of the pipeline: NEGATION HANDLING

Detects negation cue words ("not", "no", "without", "except",
"excluding", "avoid", "don't want", "dont want") and computes which
token-index RANGES they scope over. Any synonym/category match whose
token index falls inside one of these ranges is treated as EXCLUDED
rather than desired.

Scoping rule: a negation scope starts right after the cue word and
extends until whichever comes first:
    - a clause boundary (the "," marker inserted by normalize.py)
    - a strong contrastive conjunction ("but", "although", "however")
    - the end of the token stream
    - a hard cap of MAX_SCOPE_WORDS tokens (keeps runaway sentences safe)

Filler words like "want", "a", "any" inside the scope don't stop it --
they're just not category words, so they simply won't match anything
when extract.py checks the scope.
-----------------------------------------------------------------------
"""

NEGATION_CUES = {
    "not", "no", "without", "except", "excluding", "avoid", "avoiding",
    "exclude", "excluding", "skip", "skipping", "besides", "leave",
    "never", "none", "nothing", "unless", "walang", "huwag", "ayaw",
}

# Two-word cues are checked separately since our tokens are single words.
NEGATION_CUE_BIGRAMS = {
    "do not", "does not", "did not", "would not", "rather not",
    "dont want", "do not want", "does not want", "did not want",
    "anything but", "everything but", "all but", "other than",
    "apart from", "leave out", "skip over",
}

SCOPE_BREAKERS = {
    "but", "although", "however", "though", "yet", "i", "we", "you",
    "want", "prefer", "recommend", "show", "give", "find",
}
MAX_SCOPE_WORDS = 6


def find_negation_scopes(tokens):
    """Returns a list of (start_index, end_index) inclusive ranges (indices into `tokens`) that are under negation scope."""
    scopes = []

    for i in range(len(tokens)):
        cue_length = 0

        # "I do not mind comedy" expresses openness, not exclusion.
        if (
            tokens[i] == "not" and i + 1 < len(tokens) and tokens[i + 1] == "mind"
        ) or (
            tokens[i] == "do" and i + 2 < len(tokens)
            and tokens[i + 1] == "not" and tokens[i + 2] == "mind"
        ):
            continue

        if (
            i + 2 < len(tokens)
            and f"{tokens[i]} {tokens[i + 1]} {tokens[i + 2]}" in NEGATION_CUE_BIGRAMS
        ):
            cue_length = 3
        elif i + 1 < len(tokens) and f"{tokens[i]} {tokens[i + 1]}" in NEGATION_CUE_BIGRAMS:
            cue_length = 2
        elif tokens[i] in NEGATION_CUES:
            cue_length = 1

        if cue_length == 0:
            continue

        scope_start = i + cue_length
        scope_end = scope_start - 1  # exclusive-of-nothing sentinel

        j = scope_start
        while j < len(tokens) and j < scope_start + MAX_SCOPE_WORDS:
            if tokens[j] == "," or tokens[j] in SCOPE_BREAKERS:
                break
            scope_end = j
            j += 1

        if scope_end >= scope_start:
            scopes.append((scope_start, scope_end))

    # In broad requests, "every genre but horror" means horror is excluded.
    for i, token in enumerate(tokens):
        if token != "but" or i + 1 >= len(tokens):
            continue
        prior = tokens[max(0, i - 4):i]
        if any(word in prior for word in {"every", "all", "any", "anything", "everything"}):
            scopes.append((i + 1, len(tokens) - 1))

    return scopes


def is_within_negation_scope(start_index, end_index, scopes):
    """True if the (start_index, end_index) span of a matched phrase overlaps any negation scope."""
    return any(start_index <= e and end_index >= s for s, e in scopes)


def is_retroactively_negated(end_index, tokens):
    """Handles phrases like "horror movies are not wanted"."""
    for index in range(end_index + 1, min(len(tokens), end_index + 5)):
        if tokens[index] == "," or tokens[index] in SCOPE_BREAKERS:
            return False
        if tokens[index] == "not":
            return True
    return False
