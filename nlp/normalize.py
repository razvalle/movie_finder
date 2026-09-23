"""
nlp/normalize.py
-----------------------------------------------------------------------
STEP 2 of the pipeline: TEXT NORMALIZATION
Lowercases, expands contractions (so negation detection is reliable),
strips punctuation we don't need, and collapses whitespace.
-----------------------------------------------------------------------
"""

import re

# Contractions must be expanded BEFORE punctuation stripping, otherwise
# "don't" -> "dont" would silently lose the negation cue "not".
CONTRACTIONS = [
    (re.compile(r"\bwon't\b"), "will not"),
    (re.compile(r"\bwont\b"), "will not"),
    (re.compile(r"\bcan't\b"), "cannot"),
    (re.compile(r"\bcant\b"), "cannot"),
    (re.compile(r"\bcannot\b"), "can not"),
    (re.compile(r"n't\b"), " not"),  # no leading \b: "n" is always preceded by a letter
    (re.compile(r"\bdont\b"), "do not"),
    (re.compile(r"\bdoesnt\b"), "does not"),
    (re.compile(r"\bdidnt\b"), "did not"),
    (re.compile(r"\bwouldnt\b"), "would not"),
    (re.compile(r"\bcouldnt\b"), "could not"),
    (re.compile(r"\bisnt\b"), "is not"),
    (re.compile(r"\barent\b"), "are not"),
    (re.compile(r"\bwasnt\b"), "was not"),
    (re.compile(r"\bwerent\b"), "were not"),
    (re.compile(r"\bi'm\b"), "i am"),
    (re.compile(r"\bi've\b"), "i have"),
    (re.compile(r"\bi'd\b"), "i would"),
    (re.compile(r"\bi'll\b"), "i will"),
    (re.compile(r"\bwhat's\b"), "what is"),
    (re.compile(r"\bwhats\b"), "what is"),
    (re.compile(r"\byou're\b"), "you are"),
    (re.compile(r"\bit's\b"), "it is"),
    (re.compile(r"\bthat's\b"), "that is"),
    (re.compile(r"\blet's\b"), "let us"),
]

PUNCTUATION_RE = re.compile(r"[,.;:!?()\"'\u2014]")  # includes em dash
WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(raw_input):
    """
    Normalizes raw user input into clean lowercase text ready for
    tokenization. Keeps digits, letters, spaces, and hyphens (hyphens
    matter for tags like "sci-fi" and "rom-com").
    """
    if not isinstance(raw_input, str):
        return ""

    text = raw_input.lower().strip()
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    text = re.sub(r"\bu\s*[.]?\s*k\s*[.]?(?=\W|$)", "uk", text)
    text = re.sub(r"\bu\s*[.]?\s*s\s*[.]?(?=\W|$)", "us", text)
    text = re.sub(r"\bp\s*[.]?\s*h\s*[.]?(?=\W|$)", "ph", text)
    text = re.sub(r"\bj\s*[.]?\s*p\s*[.]?(?=\W|$)", "jp", text)
    text = re.sub(r"\bs\s*[.]?\s*k\s*[.]?(?=\W|$)", "sk", text)
    text = re.sub(r"\bk\s*[.]?\s*r\s*[.]?(?=\W|$)", "kr", text)
    text = re.sub(r"\bdon[?]\s*t\b", "dont", text)
    text = re.sub(r"\bdon\s+t\b", "dont", text)

    for pattern, replacement in CONTRACTIONS:
        text = pattern.sub(replacement, text)

    # Keep a space around commas/periods/etc so they still act as clause
    # boundaries for negation scoping, but drop the punctuation character
    # itself. Hyphens stay attached to words (sci-fi, rom-com).
    text = PUNCTUATION_RE.sub(" , ", text)
    text = WHITESPACE_RE.sub(" ", text).strip()

    return text
