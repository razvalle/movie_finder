"""Selected major international film-award wins for recognizable titles."""

AWARDS_BY_FILM = {
    ("gladiator", 2000): ["Academy Award for Best Picture"],
    ("a beautiful mind", 2001): ["Academy Award for Best Picture"],
    ("chicago", 2002): ["Academy Award for Best Picture"],
    ("the lord of the rings: the return of the king", 2003): ["Academy Award for Best Picture"],
    ("million dollar baby", 2004): ["Academy Award for Best Picture"],
    ("crash", 2005): ["Academy Award for Best Picture"],
    ("the departed", 2006): ["Academy Award for Best Picture"],
    ("no country for old men", 2007): ["Academy Award for Best Picture"],
    ("slumdog millionaire", 2008): ["Academy Award for Best Picture"],
    ("the hurt locker", 2009): ["Academy Award for Best Picture"],
    ("the king's speech", 2010): ["Academy Award for Best Picture"],
    ("the artist", 2011): ["Academy Award for Best Picture"],
    ("argo", 2012): ["Academy Award for Best Picture"],
    ("12 years a slave", 2013): ["Academy Award for Best Picture"],
    ("birdman", 2014): ["Academy Award for Best Picture"],
    ("spotlight", 2015): ["Academy Award for Best Picture"],
    ("moonlight", 2016): ["Academy Award for Best Picture"],
    ("the shape of water", 2017): ["Academy Award for Best Picture"],
    ("green book", 2018): ["Academy Award for Best Picture"],
    ("parasite", 2019): ["Academy Award for Best Picture", "Cannes Palme d'Or"],
    ("nomadland", 2020): ["Academy Award for Best Picture", "Venice Golden Lion"],
    ("coda", 2021): ["Academy Award for Best Picture"],
    ("everything everywhere all at once", 2022): ["Academy Award for Best Picture"],
    ("oppenheimer", 2023): ["Academy Award for Best Picture"],
    ("anora", 2024): ["Academy Award for Best Picture", "Cannes Palme d'Or"],
    ("crouching tiger, hidden dragon", 2000): ["Academy Award for Best Foreign Language Film"],
    ("spirited away", 2001): ["Academy Award for Best Animated Feature"],
    ("the lives of others", 2006): ["Academy Award for Best Foreign Language Film"],
    ("a separation", 2011): ["Academy Award for Best Foreign Language Film", "Berlin Golden Bear"],
    ("amour", 2012): ["Cannes Palme d'Or", "Academy Award for Best Foreign Language Film"],
    ("son of saul", 2015): ["Academy Award for Best Foreign Language Film", "Cannes Grand Prix"],
    ("the salesman", 2016): ["Academy Award for Best Foreign Language Film"],
    ("roma", 2018): ["Academy Award for Best Director", "Venice Golden Lion"],
    ("drive my car", 2021): ["Academy Award for Best International Feature"],
    ("all quiet on the western front", 2022): ["Academy Award for Best International Feature"],
    ("anatomy of a fall", 2023): ["Cannes Palme d'Or"],
    ("the zone of interest", 2023): ["Academy Award for Best International Feature"],
    ("mad max: fury road", 2015): ["Six Academy Awards"],
    ("la la land", 2016): ["Six Academy Awards"],
    ("gravity", 2013): ["Seven Academy Awards"],
    ("whiplash", 2014): ["Three Academy Awards"],
    ("the grand budapest hotel", 2014): ["Four Academy Awards"],
    ("coco", 2017): ["Academy Award for Best Animated Feature"],
    ("spider-man: into the spider-verse", 2018): ["Academy Award for Best Animated Feature"],
    ("soul", 2020): ["Academy Award for Best Animated Feature"],
    ("encanto", 2021): ["Academy Award for Best Animated Feature"],
    ("the boy and the heron", 2023): ["Academy Award for Best Animated Feature"],
}


def annotate_awards(movies):
    for movie in movies:
        key = (movie.get("title", "").strip().casefold(), movie.get("release_year"))
        awards = AWARDS_BY_FILM.get(key)
        if awards:
            movie["notable_awards"] = awards
        else:
            movie.pop("notable_awards", None)
    return movies