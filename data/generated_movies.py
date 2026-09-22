"""Deterministic expansion records for the movie finder catalog.

These records use the same schema as the hand-curated catalog. They are
created at import time so adding more records only requires changing the
count below; the normal NLP, TF-IDF, filtering, and ranking pipeline handles
them automatically.
"""

GENRE_PROFILES = [
    ("action", "exciting", "survival", "A determined hero races through a dangerous mission", ["chase", "mission", "hero"]),
    ("adventure", "epic", "discovery", "A group of explorers crosses an unknown frontier", ["journey", "quest", "exploration"]),
    ("animation", "whimsical", "friendship", "An imaginative character discovers a bright new world", ["family", "imagination", "wonder"]),
    ("comedy", "funny", "friendship", "An unlikely group gets caught in a hilarious chain of mistakes", ["friends", "mistakes", "chaos"]),
    ("crime", "dark", "conspiracy", "A clever investigator follows a dangerous trail through the underworld", ["crime", "investigation", "betrayal"]),
    ("drama", "emotional", "resilience", "A family faces a difficult choice and finds the strength to continue", ["family", "loss", "healing"]),
    ("family", "heartwarming", "friendship", "A family learns an unexpected lesson during an unforgettable journey", ["family", "children", "home"]),
    ("fantasy", "whimsical", "redemption", "A reluctant hero enters a magical realm to restore a broken promise", ["magic", "kingdom", "quest"]),
    ("horror", "scary", "survival", "A group of strangers confronts a terrifying presence in an isolated place", ["haunting", "fear", "survival"]),
    ("music", "uplifting", "identity", "A gifted musician finds a new voice while chasing an impossible dream", ["music", "performance", "dream"]),
    ("musical", "romantic", "ambition", "Two performers find love while preparing for a life-changing show", ["music", "dance", "performance"]),
    ("mystery", "suspenseful", "investigation", "A determined detective uncovers hidden clues behind a puzzling case", ["detective", "clues", "investigation"]),
    ("romance", "romantic", "true love", "Two people from different worlds discover an unexpected connection", ["love", "relationship", "heart"]),
    ("sci-fi", "thought-provoking", "identity", "A scientist confronts an impossible discovery that changes humanity's future", ["space", "technology", "future"]),
    ("thriller", "tense", "conspiracy", "An ordinary person races to expose a conspiracy before time runs out", ["danger", "secrets", "chase"]),
]

MOOD_ALTERNATES = {
    "action": ["intense", "energetic"],
    "adventure": ["exciting", "uplifting"],
    "animation": ["gentle", "charming"],
    "comedy": ["light", "quirky"],
    "crime": ["tense", "gritty"],
    "drama": ["sad", "hopeful"],
    "family": ["gentle", "uplifting"],
    "fantasy": ["epic", "charming"],
    "horror": ["tense", "unsettling"],
    "music": ["emotional", "inspiring"],
    "musical": ["funny", "uplifting"],
    "mystery": ["clever", "dark"],
    "romance": ["emotional", "heartwarming"],
    "sci-fi": ["epic", "intense"],
    "thriller": ["dark", "suspenseful"],
}


def build_generated_movies(count=1000, first_id=66):
    """Build unique, schema-valid catalog records without external services."""
    movies = []
    for offset in range(count):
        genre, mood, theme, synopsis_start, keywords = GENRE_PROFILES[offset % len(GENRE_PROFILES)]
        alternate_moods = MOOD_ALTERNATES[genre]
        release_year = 1970 + ((offset * 7) % 57)
        runtime = 80 + ((offset * 11) % 61)
        movies.append({
            "id": first_id + offset,
            "title": f"{genre.replace('-', ' ').title()} Story {offset + 1:04d}",
            "synopsis": f"{synopsis_start} in chapter {offset + 1}, where {keywords[0]} and {keywords[1]} reveal a deeply personal secret.",
            "genres": [genre],
            "runtime": runtime,
            "release_year": release_year,
            "themes": [theme],
            "mood_tags": [mood, *alternate_moods],
            "keywords": keywords,
            "content_descriptors": [],
        })
    return movies


GENERATED_MOVIES = build_generated_movies()
