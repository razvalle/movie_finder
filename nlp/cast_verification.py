"""Cast/gender verification for billed-cast evidence and strict no-women queries."""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
CAST_EXPLANATION_PROMPT = (PROMPTS_DIR / "cast_gender_explanation.txt").read_text(encoding="utf-8")

FEMALE_HINT_WORDS = {
    "woman", "women", "female", "girl", "girls", "mother", "queen", "princess",
    "sister", "daughter", "wife", "widow", "lady", "mistress", "she", "her",
}

NON_BINARY_GENDER_VALUES = {3, "nonbinary", "non-binary", "genderqueer", "fluid"}


def _normalize_cast_entry(raw_entry, order):
    if isinstance(raw_entry, str):
        return {"name": raw_entry.strip(), "character": "", "billing_order": order, "gender": 0}
    if not isinstance(raw_entry, dict):
        return {"name": "", "character": "", "billing_order": order, "gender": 0}
    gender = raw_entry.get("gender")
    if gender in (1, 2, 3):
        normalized_gender = int(gender)
    elif isinstance(gender, str):
        key = gender.strip().lower()
        normalized_gender = {
            "female": 1,
            "woman": 1,
            "f": 1,
            "male": 2,
            "man": 2,
            "m": 2,
            "nonbinary": 3,
            "non-binary": 3,
            "nb": 3,
        }.get(key, 0)
    else:
        normalized_gender = 0
    return {
        "name": (raw_entry.get("name") or "").strip(),
        "character": (raw_entry.get("character") or raw_entry.get("character_name") or "").strip(),
        "billing_order": order,
        "gender": normalized_gender,
    }


def _coerce_cast(movie):
    cast = movie.get("cast") or movie.get("actors") or []
    if not isinstance(cast, list):
        return []
    cleaned = []
    for index, entry in enumerate(cast[:30], start=1):
        cleaned.append(_normalize_cast_entry(entry, index))
    return cleaned


def _match_female_hint(text):
    if not text:
        return []
    haystack = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    hints = []
    for token in sorted(FEMALE_HINT_WORDS, key=len, reverse=True):
        if token in haystack:
            hints.append(token)
    return hints


def build_cast_evidence(movie, top_n=15):
    cast = _coerce_cast(movie)
    top_cast = cast[:max(1, top_n)]
    female_count = sum(1 for person in top_cast if person.get("gender") == 1)
    male_count = sum(1 for person in top_cast if person.get("gender") == 2)
    nonbinary_count = sum(1 for person in top_cast if person.get("gender") == 3)
    unspecified_count = sum(1 for person in top_cast if person.get("gender") == 0)
    known_gender_count = female_count + male_count + nonbinary_count
    coverage = (known_gender_count / len(top_cast)) if top_cast else 0.0
    names = " ".join(person.get("name", "") for person in top_cast)
    characters = " ".join(person.get("character", "") for person in top_cast)
    overview = (movie.get("overview") or movie.get("synopsis") or "") + " " + names + " " + characters
    female_character_mentions = _match_female_hint(overview)
    narrative_context = (
        "animated" in (movie.get("genres") or [])
        or "animation" in (movie.get("genres") or [])
        or "documentary" in (movie.get("genres") or [])
    )
    reason_base = (
        "Top {size} billed cast: {male} male, {female} female, {nonbinary} non-binary, {unspecified} unspecified "
        "(gender known for {coverage:.0%})."
    ).format(
        size=len(top_cast),
        male=male_count,
        female=female_count,
        nonbinary=nonbinary_count,
        unspecified=unspecified_count,
        coverage=coverage,
    )
    if narrative_context:
        reason_base += " Animated/documentary credits are treated as supporting evidence only."
    if female_character_mentions:
        reason_base += f" Female cues in overview/character text: {', '.join(sorted(set(female_character_mentions)))}."
    return {
        "top_cast_size": len(top_cast),
        "female_count": female_count,
        "male_count": male_count,
        "nonbinary_count": nonbinary_count,
        "unspecified_count": unspecified_count,
        "known_gender_count": known_gender_count,
        "gender_coverage": round(coverage, 3),
        "female_character_mentions": sorted(set(female_character_mentions)),
        "cast": top_cast,
        "evidence_line": reason_base,
        "narrative_context": narrative_context,
    }


def classify_cast_gender(movie, top_n=15):
    evidence = build_cast_evidence(movie, top_n)
    female_count = evidence["female_count"]
    coverage = evidence["gender_coverage"]
    female_mentions = bool(evidence["female_character_mentions"])
    if female_mentions:
        return {
            "status": "contradicted",
            "badge": "Contradicted",
            "reason": "Female-character cues appear in the available narrative metadata.",
            "evidence": evidence,
            "contradicted": True,
        }

    if not evidence["cast"]:
        return {
            "status": "unknown",
            "badge": "Unverified",
            "reason": "No billed-cast metadata is available, so this cannot be confirmed.",
            "evidence": evidence,
            "contradicted": False,
        }

    if female_count > 0:
        return {
            "status": "contradicted",
            "badge": "Contradicted",
            "reason": "Female cast or female-character cues are present in the billed cast and/or plot metadata.",
            "evidence": evidence,
            "contradicted": True,
        }

    if evidence["top_cast_size"] >= 5 and coverage >= 0.9 and female_count == 0:
        return {
            "status": "strong",
            "badge": "Strong evidence",
            "reason": "No women were found among the top billed cast records; this does not prove absence from the entire film.",
            "evidence": evidence,
            "contradicted": False,
        }

    if evidence["top_cast_size"] >= 5 and coverage >= 0.7 and female_count == 0:
        return {
            "status": "moderate",
            "badge": "Closest match",
            "reason": "The top billed cast appears mostly male, but some gender metadata is unverified.",
            "evidence": evidence,
            "contradicted": False,
        }

    if coverage < 0.7 or evidence["top_cast_size"] < 5:
        return {
            "status": "weak",
            "badge": "Unverified",
            "reason": "Can't verify; the billed cast has low gender coverage or missing credits.",
            "evidence": evidence,
            "contradicted": False,
        }

    return {
        "status": "unknown",
        "badge": "Unverified",
        "reason": "Cast data is incomplete or ambiguous, so this cannot be confirmed.",
        "evidence": evidence,
        "contradicted": False,
    }


def evaluate_cast_constraint(movie, constraint):
    if not constraint or constraint.get("type") != "cast_gender":
        return True
    result = classify_cast_gender(movie)
    requirement = constraint.get("require") or constraint.get("exclude")
    if constraint.get("exclude") == "female":
        return not result["contradicted"]
    if requirement == "all_male":
        return not result["contradicted"]
    evidence = result["evidence"]
    if requirement == "all_female":
        return evidence["female_count"] > 0 and evidence["male_count"] == 0 and evidence["gender_coverage"] >= 0.7
    if requirement == "female_lead":
        return any(person.get("gender") == 1 for person in evidence["cast"][:3])
    if requirement == "majority_women":
        return evidence["female_count"] > evidence["male_count"]
    if requirement == "at_least_one_woman":
        return evidence["female_count"] > 0 or bool(evidence["female_character_mentions"])
    if requirement == "no_men":
        return evidence["male_count"] == 0 and evidence["female_count"] > 0 and evidence["gender_coverage"] >= 0.7
    return True


def explain_cast_verification(movie, constraint=None, status=None):
    status = status or classify_cast_gender(movie)
    evidence = status["evidence"]
    reason = status["reason"]
    if constraint:
        reason = f"{reason} Constraint: {constraint}."
    if evidence["female_character_mentions"]:
        reason += " Female-character hints are treated as supporting evidence only."
    return {
        "status": status["status"],
        "badge": status["badge"],
        "reason": reason,
        "evidence_line": evidence["evidence_line"],
        "prompt": CAST_EXPLANATION_PROMPT,
    }
