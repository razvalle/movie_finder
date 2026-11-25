"""
nlp/runtime.py
-----------------------------------------------------------------------
STEP 7 of the pipeline: RUNTIME / NUMBER EXTRACTION

Parses natural-language runtime constraints out of the ORIGINAL
(normalized, but not stopword-stripped) text and converts them into a
numeric constraint dict:
    {"min": number|None, "max": number|None, "target": number|None, "tolerance": number}

Supported patterns (word numbers AND digits, hours AND minutes):
    "under two hours"             -> max = 119
    "less than 120 minutes"       -> max = 119
    "no more than 100 minutes"    -> max = 100
    "not longer than 100 minutes" -> max = 100
    "at least two hours"          -> min = 120
    "more than 90 minutes"        -> min = 91
    "around 90 minutes"           -> target = 90, tolerance = 15
    "about two hours"             -> target = 120, tolerance = 15
-----------------------------------------------------------------------
"""

import re

from .catalog_matching import load_query_rules

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
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
    word_number = re.fullmatch(r"(\w+)(?:\s+(\w+))?", trimmed)
    if word_number:
        first = WORD_NUMBERS.get(word_number.group(1))
        second = WORD_NUMBERS.get(word_number.group(2)) if word_number.group(2) else None
        if first is not None and second is not None and first >= 20 and second < 10:
            return first + second

    if trimmed in WORD_NUMBERS:
        return WORD_NUMBERS[trimmed]
    return None


_WORD_NUMBER_PATTERN = "|".join(sorted(WORD_NUMBERS, key=len, reverse=True))
NUM = rf"(\d+(?:\.\d+)?|(?:{_WORD_NUMBER_PATTERN})(?:\s+(?:{_WORD_NUMBER_PATTERN}))?(?:\s+and a half)?)"


def to_minutes(value, unit):
    """Converts a matched (value, unit) into minutes."""
    if value is None:
        return None
    return round(value * 60) if "hour" in unit else round(value)


def _percentile(values, percentile):
    ordered = sorted(value for value in values if isinstance(value, (int, float)) and value > 0)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile / 100)))
    return int(ordered[index])


def extract_vague_runtime_constraint(normalized_text, movie_runtimes):
    """Translate vague duration terms to catalog-derived percentile bounds."""
    rules = load_query_rules()["runtime_percentiles"]
    text = normalized_text.casefold()
    result = {"min": None, "max": None, "target": None, "tolerance": 15}
    if re.search(r"\bnot too long\b", text):
        bound = _percentile(movie_runtimes, rules["not too long"])
        if bound is not None:
            result["max"] = bound
            return result
    if re.search(r"\b(?:short|quick|brief|not too long)\b", text):
        phrase = "quick" if re.search(r"\bquick\b", text) else "short"
        bound = _percentile(movie_runtimes, rules[phrase])
        if bound is not None:
            result["max"] = bound
            return result
    if re.search(r"\blong\b", text):
        bound = _percentile(movie_runtimes, rules["long"])
        if bound is not None:
            result["min"] = bound
            return result
    return None


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

    normalized_text = re.sub(
        r"\b(\d{1,2}):(\d{2})\b",
        lambda match: (
            f"{int(match.group(1)) * 60 + int(match.group(2))} minutes"
            if int(match.group(2)) < 60 else match.group(0)
        ),
        normalized_text,
    )
    normalized_text = re.sub(
        r"\b(\d+)h(\d{1,2})\b",
        lambda match: (
            f"{int(match.group(1)) * 60 + int(match.group(2))} minutes"
            if int(match.group(2)) < 60 else match.group(0)
        ),
        normalized_text,
    )
    normalized_text = re.sub(
        r"\b(an?|one) hour and a half\b",
        "ninety minutes",
        normalized_text,
    )

    range_pattern = re.compile(
        r"\b(?:(?:between|from)\s+)?"
        + NUM + r"\s*(hours?|minutes?|hrs?|mins?)?\s*"
        r"(?:-|to|and)\s*"
        + NUM + r"\s*(hours?|minutes?|hrs?|mins?)?\b"
    )
    range_match = range_pattern.search(normalized_text)
    if range_match:
        first_value = parse_number_phrase(range_match.group(1))
        second_value = parse_number_phrase(range_match.group(3))
        first_unit = range_match.group(2)
        second_unit = range_match.group(4)
        inherited_unit = first_unit or second_unit or "minutes"
        minimum = to_minutes(first_value, first_unit or inherited_unit)
        maximum = to_minutes(second_value, second_unit or inherited_unit)
        if not first_unit and not second_unit and max(first_value or 0, second_value or 0) > 600:
            range_match = None
        if range_match and minimum is not None and maximum is not None:
            result["min"], result["max"] = minimum, maximum
            return result

    strict_rules = [
        ("max", re.compile(
            r"(?:under|below|less than|shorter than)\s+"
            + NUM + r"\s*(hours?|minutes?|hrs?|mins?)"
        ), -1),
        ("min", re.compile(
            r"(?:over|(?<!no )more than|(?<!not )longer than|above)\s+"
            + NUM + r"\s*(hours?|minutes?|hrs?|mins?)"
        ), 1),
    ]
    for kind, pattern, adjustment in strict_rules:
        for match in pattern.finditer(normalized_text):
            minutes = to_minutes(parse_number_phrase(match.group(1)), match.group(2))
            if minutes is None:
                continue
            value = minutes + adjustment
            if result[kind] is None:
                result[kind] = value
            elif kind == "max":
                result[kind] = min(result[kind], value)
            else:
                result[kind] = max(result[kind], value)

    for kind, pattern in RULES:
        for match in pattern.finditer(normalized_text):
            value = parse_number_phrase(match.group(1))
            minutes = to_minutes(value, match.group(2))
            if minutes is None:
                continue
            if result[kind] is None:
                result[kind] = minutes
            elif kind == "max":
                result[kind] = min(result[kind], minutes)
            elif kind == "min":
                result[kind] = max(result[kind], minutes)

    if (result["min"] is not None or result["max"] is not None) and not re.search(
        r"\b(?:around|about|roughly|approximately|close to)\b",
        normalized_text,
    ):
        result["target"] = None
    return result
