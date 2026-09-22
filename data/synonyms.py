"""
data/synonyms.py
-----------------------------------------------------------------------
SYNONYM DICTIONARY
-----------------------------------------------------------------------
This is the ONLY file that maps free-text words/phrases to the
canonical vocabulary used by the dataset (data/movies.py).

Structure: canonical_tag -> list of surface forms a user might type.
The canonical tag itself does NOT need to be repeated in its own list
(it is added automatically by build_reverse_index()).

Multi-word phrases are supported (e.g. "feel good", "edge of your
seat") -- the matcher checks trigrams/bigrams before single words.

TO EDIT: just add/remove words from these lists, or add a new
canonical key. No other file needs to change -- extract.py reads this
dictionary dynamically through build_reverse_index().
-----------------------------------------------------------------------
"""

MOOD_SYNONYMS = {
    "suspenseful": ["suspense", "thriller", "thrilling", "tense", "edge of your seat", "nail biting", "gripping"],
    "scary": ["scare", "scared", "horror", "frightening", "terrifying", "spooky", "creepy", "eerie"],
    "funny": ["funny", "comedy", "humorous", "hilarious", "comedic", "silly", "goofy", "witty"],
    "sad": ["sad", "emotional", "tragic", "tearjerker", "tear jerker", "heartbreaking", "heartbroken", "grieving", "grief-stricken", "depressing", "somber"],
    "romantic": ["romance", "romantic", "love story", "loving", "sweet"],
    "happy": ["happy", "uplifting", "feel good", "heartwarming", "wholesome", "cheerful"],
    "dark": ["dark", "gritty", "bleak", "grim", "disturbing"],
    "exciting": ["exciting", "thrilling", "action packed", "adrenaline", "energetic", "fast paced"],
    "relaxing": ["relaxing", "calm", "chill", "light", "easygoing", "cozy", "gentle"],
    "thought-provoking": ["thought provoking", "philosophical", "deep", "mind bending", "cerebral"],
    "whimsical": ["whimsical", "quirky", "magical", "dreamy", "charming"],
    "epic": ["epic", "grand", "sweeping", "awe inspiring"],
    "nostalgic": ["nostalgic", "nostalgia"],
    "intense": ["intense", "gripping", "hard hitting"],
}

# NOTE: some surface forms above (e.g. "horror" under "scary", "comedy"
# under "funny", "romance" under "romantic") intentionally overlap with
# genre vocabulary below. build_reverse_index() resolves the overlap by
# registering GENRE_SYNONYMS first, so bare genre nouns ("horror",
# "comedy", "romance", "thriller") always resolve to the genre, while
# their mood-adjective forms ("scary", "funny", "romantic", "suspenseful")
# are unaffected since those exact words never appear in GENRE_SYNONYMS.

GENRE_SYNONYMS = {
    "horror": ["horror", "horrors", "horror film", "horror films", "scary movie", "slasher", "slashers"],
    "comedy": ["comedy", "comedies", "sitcom"],
    "romance": ["romance", "romances", "love story", "love stories", "rom com", "rom-com", "romcom"],
    "drama": ["drama", "dramas", "dramatic movie", "dramatic film"],
    "mystery": ["mystery", "mysteries", "mystery movie", "mystery film", "whodunit", "detective story", "detective stories"],
    "thriller": ["thriller", "thrillers"],
    "action": ["action", "action movie", "action movies"],
    "sci-fi": ["sci fi", "sci-fi movie", "sci-fi movies", "science fiction", "scifi"],
    "fantasy": ["fantasy"],
    "animation": ["animation", "animated", "animated movie", "cartoon", "cartoons"],
    "crime": ["crime", "crime drama", "crime dramas", "gangster movie", "gangster movies"],
    "adventure": ["adventure"],
    "family": ["family movie", "family movies", "kids movie", "kids movies", "family friendly", "family-friendly"],
    "musical": ["musical", "musicals", "music movie", "music movies"],
    "music": ["music movie", "music movies", "musician movie", "musician movies"],
    "documentary": ["documentary", "documentaries", "docu"],
}

THEME_SYNONYMS = {
    "family secrets": ["family secrets", "family drama"],
    "investigation": ["investigation", "detective", "whodunit", "cold case"],
    "conspiracy": ["conspiracy", "cover up"],
    "revenge": ["revenge", "vengeance"],
    "redemption": ["redemption"],
    "coming of age": ["coming of age", "growing up"],
    "time loop": ["time loop", "groundhog day plot", "repeating day"],
    "time travel": ["time travel", "travels through time", "travel through time", "time traveler", "time traveller"],
    "heist": ["heist", "robbery"],
    "survival": ["survival", "survive"],
    "space exploration": ["space", "outer space", "spaceship"],
    "post-apocalypse": ["post apocalyptic", "apocalypse", "dystopia", "dystopian"],
    "true love": ["true love", "soulmate"],
    "friendship": ["friendship", "friends"],
    "identity": ["identity", "self discovery"],
    "grief": ["grief", "loss", "mourning"],
    "class conflict": ["class conflict", "class divide", "rich vs poor"],
    "haunting": ["haunted", "haunting", "ghost story", "haunted house", "haunting house"],
    "possession": ["possession", "possessed", "demonic"],
    "serial killer": ["serial killer", "killer on the loose"],
    "mental health": ["mental health", "mental illness", "mental institution", "healing from trauma"],
    "cultural identity": ["cultural identity", "culture clash"],
}

RELEASE_ERA_SYNONYMS = {
    "classic": ["classic", "old movie", "old school"],
    "recent": ["recent", "new", "new release", "modern"],
}


def build_reverse_index():
    """
    Builds a reverse lookup: surface phrase (lowercase, space-separated)
    -> {"category": "mood"|"genre"|"theme", "tag": canonical_tag}

    First registration wins a collision. Genre nouns ("horror",
    "thriller", "romance") are registered before mood adjectives
    ("scary", "suspenseful", "romantic") on purpose: when a word is
    genuinely a genre name, treat it as genre.
    """
    index = {}

    def register(dictionary, category):
        for canonical, surface_forms in dictionary.items():
            all_forms = set([canonical, *surface_forms])
            for form in all_forms:
                key = form.lower().strip()
                if key not in index:
                    index[key] = {"category": category, "tag": canonical}

    register(GENRE_SYNONYMS, "genre")
    register(MOOD_SYNONYMS, "mood")
    register(THEME_SYNONYMS, "theme")

    return index


MAX_PHRASE_LENGTH = 3  # longest surface form is 3 words ("edge of your seat")
