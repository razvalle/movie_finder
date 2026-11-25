"""
nlp/release_year.py
-----------------------------------------------------------------------
Optional release-year preference extraction. Supports:
    "from the 90s" / "from the 1990s"   -> {"min": 1990, "max": 1999}
    "before 2000"                        -> {"min": None, "max": 1999}
    "after 2015" / "since 2015"          -> {"min": 2015, "max": None}
    "recent" / "new" / "modern"          -> {"min": current_year-6, "max": None}
    "classic" / "old"                    -> {"min": None, "max": 1995}
Returns None if no year preference is detected (so scoring.py can skip
this factor entirely rather than penalizing unspecified movies).
-----------------------------------------------------------------------
"""

import re
from datetime import date

CURRENT_YEAR = date.today().year


def extract_release_year_constraint(normalized_text):
    match = re.search(r"\b(19\d{2}|20\d{2})\s*(?:-|to|through|until)\s*(19\d{2}|20\d{2})\b", normalized_text)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        return {"min": start, "max": end}

    minimums = []
    maximums = []
    for match in re.finditer(r"\b(after|since|starting in)\s+(19\d{2}|20\d{2})\b", normalized_text):
        offset = 1 if match.group(1) == "after" else 0
        minimums.append(int(match.group(2)) + offset)
    for match in re.finditer(r"\b(before|until)\s+(19\d{2}|20\d{2})\b", normalized_text):
        offset = 0 if match.group(1) == "until" else -1
        maximums.append(int(match.group(2)) + offset)
    if minimums or maximums:
        return {
            "min": max(minimums) if minimums else None,
            "max": min(maximums) if maximums else None,
        }

    match = re.search(r"\b(?:from the |from |in )?(\d{2}|\d{4})s\b", normalized_text)
    if match:
        decade = int(match.group(1))
        if decade < 100:
            decade += 2000 if decade < 30 else 1900  # "20s"->2020s, "90s"->1990s
        return {"min": decade, "max": decade + 9}

    spelled_decades = {
        "twenties": 1920, "thirties": 1930, "forties": 1940,
        "fifties": 1950, "sixties": 1960, "seventies": 1970,
        "eighties": 1980, "nineties": 1990,
    }
    match = re.search(
        r"\b(?:from the |from |in )?(twenties|thirties|forties|fifties|"
        r"sixties|seventies|eighties|nineties)\b",
        normalized_text,
    )
    if match:
        decade = spelled_decades[match.group(1)]
        return {"min": decade, "max": decade + 9}

    match = re.search(r"before (\d{4})", normalized_text)
    if match:
        return {"min": None, "max": int(match.group(1)) - 1}

    match = re.search(r"(after|since) (\d{4})", normalized_text)
    if match:
        offset = 0 if match.group(1) == "since" else 1
        return {"min": int(match.group(2)) + offset, "max": None}

    match = re.search(r"\b(19\d{2}|20\d{2})\b", normalized_text)
    if match:
        year = int(match.group(1))
        return {"min": year, "max": year}

    if re.search(r"\b(recent|new release|newer|modern)\b", normalized_text):
        return {"min": CURRENT_YEAR - 6, "max": None}

    if re.search(r"\b(classic|old movie|old school|older)\b", normalized_text):
        return {"min": None, "max": 1995}

    return None
