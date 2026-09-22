"""
nlp/runtime.py
-----------------------------------------------------------------------
STEP 7 of the pipeline: RUNTIME / NUMBER EXTRACTION

Parses natural-language runtime constraints out of the ORIGINAL
(normalized, but not stopword-stripped) text and converts them into a
numeric constraint dict:
    {"min": number|None, "max": number|None, "target": number|None, "tolerance": number}

Supported patterns (word numbers AND digits, hours AND minutes):
    "under two hours"             -> max = 120
    "less than 120 minutes"       -> max = 120
    "no more than 100 minutes"    -> max = 100
    "not longer than 100 minutes" -> max = 100
    "at least two hours"          -> min = 120
    "more than 90 minutes"        -> min = 90
    "around 90 minutes"           -> target = 90, tolerance = 15
    "about two hours"             -> target = 120, tolerance = 15
-----------------------------------------------------------------------
"""

import re

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "half": 0.5, "an": 1, "a": 1,
}


def parse_number_phrase(phrase):
    """Converts a captured number phrase like "two", "2", "an", "one and a half" into a float."""
    trimmed = phrase.strip()
    if re.fullmatch(r"\d+(\.\d+)?", trimmed):
        return float(trimmed)

    half_match = re.fullmatch(r"(\w+) and a half", trimmed)
    if half_match and half_match.group(1) in WORD_NUMBERS:
        return WORD_NUMBERS[half_match.group(1)] + 0.5

    if trimmed in WORD_NUMBERS:
        return WORD_NUMBERS[trimmed]
    return None


NUM = r"(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|an?|\w+ and a half)"


def to_minutes(value, unit):
    """Converts a matched (value, unit) into minutes."""
    if value is None:
        return None
    return round(value * 60) if "hour" in unit else round(value)


# Ordered rule list: first successful match wins, so a qualified match
# ("under two hours") is never overwritten by the generic bare-number
# fallback rule that comes last.
RULES = [
    ("max", re.compile(
        r"(?:under|below|less than|no more than|not more than|not longer than|shorter than|within)\s+"
        + NUM + r"\s*(hours?|minutes?|hrs?|mins?)"
    )),
    ("min", re.compile(
        r"(?:at least|over|more than|longer than|above|at minimum)\s+"
        + NUM + r"\s*(hours?|minutes?|hrs?|mins?)"
    )),
    ("target", re.compile(
        r"(?:around|about|roughly|approximately|close to)\s+"
        + NUM + r"\s*(hours?|minutes?|hrs?|mins?)"
    )),
    # Bare "two hours" / "120 minutes" with no qualifier defaults to a
    # target with tolerance.
    ("target", re.compile(NUM + r"\s*(hours?|minutes?|hrs?|mins?)(?:\s+(?:long|movie|film))?")),
]


def extract_runtime_constraint(normalized_text):
    """
    Scans normalized text for the first runtime constraint it can find.
    Returns {"min", "max", "target", "tolerance"} with None where not
    specified. tolerance is only meaningful when target is not None
    (defaults to 15 minutes).
    """
    result = {"min": None, "max": None, "target": None, "tolerance": 15}

    for kind, pattern in RULES:
        match = pattern.search(normalized_text)
        if not match:
            continue

        value = parse_number_phrase(match.group(1))
        minutes = to_minutes(value, match.group(2))
        if minutes is None:
            continue

        result[kind] = minutes
        # First successful rule wins -- stop scanning.
        break

    return result
