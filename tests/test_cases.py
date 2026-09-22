"""
tests/test_cases.py
-----------------------------------------------------------------------
TEST CASE DEFINITIONS
Each case records: input, what preferences SHOULD be extracted, and
what the top result is expected to satisfy. run_tests.py executes
these against the real pipeline (no mocking) and produces a report
with user input / extracted preferences / expected / actual /
success-or-failure / reason / improvement, per the project brief.

`expect` fields are intentionally partial checks (subset checks), not
exact-list-equality checks, since natural language is under-specified
and a good pipeline may reasonably extract a superset in edge cases.
-----------------------------------------------------------------------
"""

TEST_CASES = [
    # ---- EASY ----
    {"id": "E1", "difficulty": "easy", "input": "I want a funny movie.",
     "expect": {"moods_include": ["funny"]}},
    {"id": "E2", "difficulty": "easy", "input": "Show me a romance movie.",
     "expect": {"genres_include": ["romance"]}},
    {"id": "E3", "difficulty": "easy", "input": "I feel like watching something scary.",
     "expect": {"moods_include": ["scary"]}},

    # ---- MODERATE ----
    {"id": "M1", "difficulty": "moderate",
     "input": "I want something suspenseful but not horror, preferably a mystery under two hours.",
     "expect": {
         "moods_include": ["suspenseful"], "genres_include": ["mystery"],
         "excluded_genres_include": ["horror"], "runtime_max": 120,
     }},
    {"id": "M2", "difficulty": "moderate", "input": "Give me a funny movie, around 100 minutes.",
     "expect": {"moods_include": ["funny"], "runtime_target": 100}},
    {"id": "M3", "difficulty": "moderate", "input": "I'm in the mood for a sad romantic drama.",
     "expect": {"moods_include": ["sad", "romantic"], "genres_include": ["drama"]}},

    # ---- DIFFICULT ----
    {"id": "D1", "difficulty": "difficult",
     "input": "Something dark and suspenseful, not a comedy, at least 100 minutes but not longer than 150.",
     "expect": {
         "moods_include": ["dark", "suspenseful"], "excluded_genres_include": ["comedy"],
         # conflicting-looking runtime phrasing: our parser takes the
         # first qualified match found ("at least 100 minutes" -> min=100).
         "runtime_min": 100,
     }},
    {"id": "D2", "difficulty": "difficult",
     "input": "I don't want comedy or romance, just give me an intense action movie under two hours with a heist in it.",
     "expect": {
         "excluded_genres_include": ["comedy", "romance"], "genres_include": ["action"],
         "moods_include": ["intense"], "runtime_max": 120,
     }},
    {"id": "D3", "difficulty": "difficult",
     "input": "Nothing scary, no horror, and definitely no gore — I just want something heartwarming and family friendly, about 100 minutes.",
     "expect": {
         "excluded_moods_include": ["scary"], "excluded_genres_include": ["horror"],
         "moods_include": ["happy"], "genres_include": ["family"], "runtime_target": 100,
     }},

    # ---- SYNONYM COVERAGE ----
    {"id": "S1", "difficulty": "synonym", "input": "I want something hilarious.",
     "expect": {"moods_include": ["funny"]}},
    {"id": "S2", "difficulty": "synonym", "input": "Give me a tense, edge of your seat thriller.",
     "expect": {"moods_include": ["suspenseful"]}},
    {"id": "S3", "difficulty": "synonym", "input": "Something tragic and heartbreaking please.",
     "expect": {"moods_include": ["sad"]}},
    {"id": "S4", "difficulty": "synonym", "input": "I'd like a rom-com.",
     "expect": {"genres_include": ["romance"]}},

    # ---- DIFFERENT SENTENCE STRUCTURES ----
    {"id": "V1", "difficulty": "structure",
     "input": "horror movies are not what I want tonight, mystery is more like it",
     "expect": {"excluded_genres_include": ["horror"], "genres_include": ["mystery"]}},
    {"id": "V2", "difficulty": "structure", "input": "under 90 minutes, comedy, nothing else matters",
     "expect": {"genres_include": ["comedy"], "runtime_max": 90}},
    {"id": "V3", "difficulty": "structure",
     "input": "can you recommend a film about time travel that's also funny",
     "expect": {"moods_include": ["funny"], "themes_include": ["time travel"]}},

    # ---- MULTIPLE CONDITIONS ----
    {"id": "C1", "difficulty": "multi-condition",
     "input": "A suspenseful mystery, not horror, under 140 minutes, from after 2010.",
     "expect": {
         "moods_include": ["suspenseful"], "genres_include": ["mystery"],
         "excluded_genres_include": ["horror"], "runtime_max": 140, "has_release_year": True,
     }},

    # ---- NEGATION-HEAVY ----
    {"id": "N1", "difficulty": "negation", "input": "no scary movies, no violence, without romance",
     "expect": {"excluded_moods_include": ["scary"], "excluded_genres_include": ["romance"]}},
    {"id": "N2", "difficulty": "negation", "input": "I don't want comedy",
     "expect": {"excluded_genres_include": ["comedy"]}},
    {"id": "N3", "difficulty": "negation", "input": "not a fan of sad movies, but a little romance is fine",
     # "romance" as a bare noun resolves to the ROMANCE GENRE (genre
     # nouns take precedence over mood-adjective synonyms).
     "expect": {"excluded_moods_include": ["sad"], "genres_include": ["romance"]}},

    # ---- TAGLISH ----
    {"id": "T1", "difficulty": "taglish",
     "input": "Gusto ko ng suspenseful na mystery movie, less than 130 minutes, walang horror.",
     "expect": {"moods_include": ["suspenseful"], "genres_include": ["mystery"], "runtime_max": 130}},
    {"id": "T2", "difficulty": "taglish", "input": "Something na funny lang, mga 90 minutes, comedy movie.",
     "expect": {"moods_include": ["funny"], "genres_include": ["comedy"], "runtime_target": 90}},

    # ---- FREE-TEXT / KEYWORD FALLBACK (no dictionary hits expected) ----
    {"id": "K1", "difficulty": "keyword-fallback", "input": "a movie about a heist in a hotel with secrets",
     "expect": {"free_text_not_empty": True}},

    # ---- SMART GENRE SEARCH ----
    {"id": "G1", "difficulty": "genre-search", "input": "recommend horror",
     "expect": {"genres_include": ["horror"]}},
    {"id": "G2", "difficulty": "genre-search", "input": "i dont want comedy, recommend me horror",
     "expect": {"genres_include": ["horror"], "excluded_genres_include": ["comedy"]}},
    {"id": "G3", "difficulty": "genre-search", "input": "i like every genre except horror",
     "expect": {"excluded_genres_include": ["horror"]}},
    {"id": "G4", "difficulty": "genre-search", "input": "every genre but horror",
     "expect": {"excluded_genres_include": ["horror"]}},
    {"id": "G5", "difficulty": "genre-search", "input": "show me action or adventure movies",
     "expect": {"genres_include": ["action", "adventure"]}},
    {"id": "G6", "difficulty": "genre-search", "input": "anything besides romance",
     "expect": {"excluded_genres_include": ["romance"]}},
    {"id": "G7", "difficulty": "genre-search", "input": "give me kids movies",
     "expect": {"genres_include": ["family"]}},
    {"id": "G8", "difficulty": "genre-search", "input": "horror movies are not for me, recommend mystery",
     "expect": {"genres_include": ["mystery"], "excluded_genres_include": ["horror"]}},
    {"id": "G9", "difficulty": "genre-search", "input": "I don’t want comedy, only horror",
     "expect": {"genres_include": ["horror"], "excluded_genres_include": ["comedy"]}},
    {"id": "G10", "difficulty": "genre-search", "input": "horror unless it is comedy",
     "expect": {"genres_include": ["horror"], "excluded_genres_include": ["comedy"]}},
    {"id": "G11", "difficulty": "genre-search", "input": "I want something that isnt horror",
     "expect": {"excluded_genres_include": ["horror"]}},
    {"id": "G12", "difficulty": "genre-search", "input": "I don’t mind comedy, but prefer horror",
     "expect": {"genres_include": ["comedy", "horror"]}},
    {"id": "G13", "difficulty": "genre-search", "input": "give me family-friendly movies",
     "expect": {"genres_include": ["family"]}},

    # ---- DESCRIPTION SEARCH ----
    {"id": "D4", "difficulty": "description-search", "input": "a grieving family dealing with loss",
     "expect": {"moods_include": ["sad"], "themes_include": ["grief"]}},
    {"id": "D5", "difficulty": "description-search", "input": "a detective solving a murder in a mansion",
     "expect": {"themes_include": ["investigation"]}},
    {"id": "D6", "difficulty": "description-search", "input": "a character travels through time",
     "expect": {"themes_include": ["time travel"]}},
    {"id": "D7", "difficulty": "description-search", "input": "a haunted house with ghosts",
     "expect": {"themes_include": ["haunting"]}},
    {"id": "D8", "difficulty": "description-search", "input": "scarry movie no romance under 2 hour",
     "expect": {"moods_include": ["scary"], "excluded_genres_include": ["romance"], "runtime_max": 120}},
    {"id": "D9", "difficulty": "description-search", "input": "a movi about grif and healing",
     "expect": {"themes_include": ["grief"]}},
    {"id": "D10", "difficulty": "description-search", "input": "not horror i want mystery",
     "expect": {"genres_include": ["mystery"], "excluded_genres_include": ["horror"]}},
    {"id": "D11", "difficulty": "description-search", "input": "something dark crime but not too violent",
     "expect": {"moods_include": ["dark"], "genres_include": ["crime"]}},
]
