"""Friendly, catalog-aware feedback layered over existing search results."""


def build_assistant_message(
    query,
    result_count,
    exact_count=0,
    selected_language="",
    language_available=True,
    genres=None,
    understood_terms=None,
    missing_requirements=None,
):
    """Describe what the catalog can do without changing search behavior."""
    absent = []
    if selected_language and not language_available:
        absent.append("language")
    filter_note = "This catalog doesn't include " + " or ".join(absent) + " details yet. " if absent else ""
    if not query and absent:
        return "I can't apply that filter yet. " + filter_note.rstrip()
    if not query:
        return ""

    detected_genres = [genre for genre in (genres or []) if isinstance(genre, str)][:2]
    acknowledgment = "I picked up your interest in " + " and ".join(detected_genres) + ". " if detected_genres else ""
    if not acknowledgment:
        concepts = [term for term in (understood_terms or []) if isinstance(term, str) and term][:2]
        if concepts:
            acknowledgment = "I took that as a search for films involving " + " and ".join(concepts) + ". "
    missing = [value for value in (missing_requirements or []) if isinstance(value, str) and value][:2]

    if exact_count:
        return acknowledgment + "I found movies that fit what you asked for."
    if result_count:
        message = acknowledgment + filter_note + "We couldn't find a movie that exactly matches your request, but I found some nearby options."
        if missing:
            message += " I can't confirm " + " or ".join(missing) + " from the details in this catalog."
        message += " Try adding a genre, mood, actor, or story detail to narrow it down."
        return message
    return acknowledgment + filter_note + (
        "I couldn't find a close fit for that wording. No close matches found in this catalog. "
        "Try refining your search with a genre, mood, actor, or short plot clue."
    )


def build_alternative_queries(understanding, preferences=None):
    """Return useful query variations based on this search's interpreted intent."""
    understanding = understanding or {}
    preferences = preferences or {}
    proposed = understanding.get("alternative_queries") or []
    if proposed:
        return list(dict.fromkeys(
            value.strip() for value in proposed
            if isinstance(value, str) and value.strip()
        ))[:3]

    def values(*items):
        return list(dict.fromkeys(
            item.strip() for group in items for item in group
            if isinstance(item, str) and item.strip()
        ))

    genres = values(preferences.get("genres", []), understanding.get("genres", []))
    moods = values(preferences.get("moods", []), understanding.get("mood_tone", []))
    themes = values(preferences.get("themes", []))
    keywords = values(
        preferences.get("free_text_keywords", []),
        understanding.get("keywords", []),
        understanding.get("expanded_concepts", []),
    )
    concepts = values(moods, themes, keywords)
    queries = []

    if concepts or genres:
        description = " ".join(concepts + genres)
        queries.append(f"{description} movies")
    queries.extend(f"{genre} movies" for genre in genres)
    queries.extend(f"{concept} movies" for concept in concepts)
    return list(dict.fromkeys(queries))[:3]
