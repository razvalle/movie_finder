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

from .catalog_matching import load_query_rules


_QUERY_RULES = load_query_rules()
NEGATION_CUES = set(_QUERY_RULES["negation_cues"]) | {
    "exclude", "excluding", "skipping", "besides", "leave", "never", "none", "nothing", "unless",
}
SOFT_DEGREE_WORDS = set(_QUERY_RULES["soft_degree_words"])

# Two-word cues are checked separately since our tokens are single words.
NEGATION_CUE_BIGRAMS = {
    "do not", "does not", "did not", "would not", "rather not",
    "dont want", "do not want", "does not want", "did not want",
    "everything but", "all but",
    "apart from", "leave out", "skip over", "instead of", "rather than",
} | {
    cue for cue in _QUERY_RULES["negation_cues"] if " " in cue
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

        if tokens[i] == "no" and i + 1 < len(tokens) and tokens[i + 1] == "thanks":
            continue
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
        if tokens[i] in {"not", "nothing"} and scope_start < len(tokens) and tokens[scope_start] in SOFT_DEGREE_WORDS:
            scope_start += 1
            continue
        scope_end = scope_start - 1  # exclusive-of-nothing sentinel

        j = scope_start
        while j < len(tokens) and j < scope_start + MAX_SCOPE_WORDS:
            if tokens[j] == "," or tokens[j] in SCOPE_BREAKERS:
                break
            scope_end = j
            j += 1

        if scope_end >= scope_start:
            scopes.append((scope_start, scope_end))

    for rule in _QUERY_RULES["postposed_negation"]:
        if rule.get("scope") != "term":
            continue
        prefix, _, suffix = rule["pattern"].partition("{term}")
        prefix_tokens = prefix.split()
        suffix_tokens = suffix.split()
        prefix_matches = not prefix_tokens or tokens[:len(prefix_tokens)] == prefix_tokens
        if not prefix_matches:
            continue
        if suffix_tokens:
            suffix_length = len(suffix_tokens)
            for end in range(len(prefix_tokens) + suffix_length, len(tokens) + 1):
                if tokens[end - suffix_length:end] != suffix_tokens:
                    continue
                term_end = end - suffix_length - 1
                term_start = max(len(prefix_tokens), term_end - MAX_SCOPE_WORDS + 1)
                if term_end >= term_start:
                    scopes.append((term_start, term_end))
        elif prefix_tokens and len(tokens) > len(prefix_tokens):
            term_start = len(prefix_tokens)
            scopes.append((term_start, min(len(tokens) - 1, term_start + MAX_SCOPE_WORDS - 1)))

    # In broad requests, "every genre but horror" means horror is excluded.
    for i, token in enumerate(tokens):
        if token != "but" or i + 1 >= len(tokens):
            continue
        prior = tokens[max(0, i - 4):i]
        if any(word in prior for word in {"every", "all", "any", "anything", "everything"}):
            scopes.append((i + 1, len(tokens) - 1))

    return scopes


def find_soft_negation_scopes(tokens):
    """Return spans following data-configured degree hedges such as 'not too X'."""
    scopes = []
    for index, token in enumerate(tokens):
        if token not in {"not", "nothing"}:
            continue
        degree_index = index + 1
        if degree_index < len(tokens) and tokens[degree_index] in SOFT_DEGREE_WORDS:
            term_index = degree_index + 1
            if term_index < len(tokens):
                end = term_index - 1
                for position in range(term_index, min(len(tokens), term_index + MAX_SCOPE_WORDS)):
                    if tokens[position] == "," or tokens[position] in SCOPE_BREAKERS:
                        break
                    end = position
                if end >= term_index:
                    scopes.append((term_index, end))
    return scopes


def is_within_negation_scope(start_index, end_index, scopes):
    """True if the (start_index, end_index) span of a matched phrase overlaps any negation scope."""
    return any(start_index <= e and end_index >= s for s, e in scopes)


def is_retroactively_negated(end_index, tokens):
    """Match only data-configured postposed negation patterns."""
    for rule in _QUERY_RULES["postposed_negation"]:
        if rule.get("scope") != "term":
            continue
        _, separator, suffix = rule["pattern"].partition("{term}")
        suffix_tokens = suffix.split()
        if not separator or not suffix_tokens:
            continue
        start = end_index + 1
        end = start + len(suffix_tokens)
        if tokens[start:end] == suffix_tokens:
            return True
    return False
