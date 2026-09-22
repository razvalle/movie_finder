"""
nlp/explain.py
-----------------------------------------------------------------------
STEP 15 of the pipeline: RESULT EXPLANATION
Converts a scored movie's breakdown dict into a short list of
human-readable check/cross reason strings, in the style specified by
the project brief:
    (check) Mystery genre matched
    (check) Suspenseful mood matched
    (check) Runtime is 108 minutes
    (check) No horror tag detected
-----------------------------------------------------------------------
"""


def title_case(tag):
    return " ".join(w[:1].upper() + w[1:] if w else w for w in tag.split(" "))


def explain_match(movie, preferences, breakdown):
    reasons = []

    if breakdown["genre"]:
        for g in breakdown["genre"]["matched"]:
            reasons.append({"ok": True, "text": f"{title_case(g)} genre matched"})
        for g in breakdown["genre"]["missing"]:
            reasons.append({"ok": False, "text": f"{title_case(g)} genre not found"})

    if breakdown["mood"]:
        for m in breakdown["mood"]["matched"]:
            reasons.append({"ok": True, "text": f"{title_case(m)} mood matched"})
        for m in breakdown["mood"]["missing"]:
            reasons.append({"ok": False, "text": f"{title_case(m)} mood not found"})

    if breakdown["theme"]:
        for t in breakdown["theme"]["matched"]:
            reasons.append({"ok": True, "text": f"{title_case(t)} theme matched"})
        for t in breakdown["theme"]["missing"]:
            reasons.append({"ok": False, "text": f"{title_case(t)} theme not found"})

    if breakdown["keyword"] is not None and breakdown["keyword"] > 0.08:
        reasons.append({"ok": True, "text": "Synopsis closely matches your description"})

    if breakdown["runtime"] is not None:
        rt = preferences["runtime"]
        runtime = movie["runtime"]
        if rt["max"] is not None and runtime <= rt["max"]:
            reasons.append({"ok": True, "text": f"Runtime is {runtime} minutes (under your {rt['max']}-minute limit)"})
        elif rt["max"] is not None:
            reasons.append({"ok": False, "text": f"Runtime is {runtime} minutes (over your {rt['max']}-minute limit)"})
        elif rt["min"] is not None and runtime >= rt["min"]:
            reasons.append({"ok": True, "text": f"Runtime is {runtime} minutes (meets your {rt['min']}-minute minimum)"})
        elif rt["min"] is not None:
            reasons.append({"ok": False, "text": f"Runtime is {runtime} minutes (short of your {rt['min']}-minute minimum)"})
        elif rt["target"] is not None:
            reasons.append({"ok": breakdown["runtime"] > 0.6, "text": f"Runtime is {runtime} minutes (you asked for around {rt['target']})"})

    if breakdown["release_year"] is not None and preferences["release_year"]:
        reasons.append({"ok": breakdown["release_year"] == 1, "text": f"Released in {movie['release_year']}"})

    # Always surface confirmed exclusions that are relevant, since these
    # survived the hard filter precisely because they DON'T carry the tag.
    for excluded_genre in preferences["excluded_genres"]:
        reasons.append({"ok": True, "text": f"No {excluded_genre} tag detected"})
    for excluded_mood in preferences["excluded_moods"]:
        reasons.append({"ok": True, "text": f"No {excluded_mood} mood detected"})
    for excluded_theme in preferences["excluded_themes"]:
        reasons.append({"ok": True, "text": f"No {excluded_theme} theme detected"})

    return reasons
