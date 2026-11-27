import unittest

from nlp.match_pipeline import finalize_results, order_browse_movies


class MatchPipelineTests(unittest.TestCase):
    def setUp(self):
        self.preferences = {
            "genres": [],
            "moods": [],
            "themes": [],
            "runtime": {"min": None, "max": None, "target": None, "tolerance": 20},
            "release_year": {"min": None, "max": None},
            "excluded_genres": [],
            "excluded_moods": [],
            "excluded_themes": [],
        }

    def test_direct_evidence_never_keeps_a_zero_score(self):
        movie = {
            "id": "direct",
            "title": "The Deep",
            "synopsis": "An underwater rescue mission.",
        }
        candidate = {
            "search_score": 0,
            "query_evidence": {
                "details": [{"phrase": "underwater", "source": "synopsis"}],
                "score": 0.35,
                "strength": 0.6,
            },
        }
        finalized = finalize_results(
            [{"movie": movie, "percent": 0}],
            {"direct": candidate},
            self.preferences,
        )

        self.assertGreater(finalized[0]["percent"], 0)
        self.assertEqual(finalized[0]["match_tier"], "close")
        self.assertTrue(finalized[0]["reasons"][0]["ok"])

    def test_selected_genre_miss_is_reported_as_a_miss(self):
        movie = {"id": "drama", "title": "Quiet Days", "genres": ["drama"]}
        finalized = finalize_results(
            [{"movie": movie, "percent": 0}],
            {"drama": {}},
            self.preferences,
            selected_genres=["mystery"],
        )

        self.assertIn("Missing: Mystery genre", finalized[0]["match_reason"])
        self.assertIn(
            {"ok": False, "text": "Missing: Mystery genre"},
            finalized[0]["reasons"],
        )

    def test_zero_evidence_is_not_labeled_as_related(self):
        movie = {"id": "unverified", "title": "Unverified Movie"}
        finalized = finalize_results(
            [{"movie": movie, "percent": 0}],
            {"unverified": {}},
            self.preferences,
        )

        self.assertEqual(finalized[0]["match_tier"], "close")
        self.assertFalse(finalized[0]["reasons"][0]["ok"])

    def test_exact_title_and_tier_order_are_canonical(self):
        exact_movie = {"id": "title", "title": "Deep Blue"}
        close_movie = {
            "id": "close",
            "title": "Blue Water",
            "synopsis": "A story about the ocean.",
        }
        related_movie = {
            "id": "related",
            "title": "Ocean Nights",
            "synopsis": "The sea is associated with water.",
        }
        finalized = finalize_results(
            [
                {"movie": related_movie, "percent": 90},
                {"movie": close_movie, "percent": 30},
                {"movie": exact_movie, "percent": 2},
            ],
            {
                "related": {
                    "query_evidence": {
                        "associated_details": [{
                            "phrase": "water",
                            "concept": "ocean",
                            "source": "synopsis",
                        }],
                        "score": 0.8,
                        "strength": 0.5,
                    },
                },
                "close": {
                    "query_evidence": {
                        "details": [{"phrase": "ocean", "source": "synopsis"}],
                        "score": 0.3,
                        "strength": 0.5,
                    },
                },
                "title": {},
            },
            self.preferences,
            exact_title_ids={"title"},
        )

        self.assertEqual(
            [entry["movie"]["id"] for entry in finalized],
            ["title", "close", "related"],
        )
        self.assertEqual(finalized[0]["percent"], 100)
        self.assertEqual(finalized[0]["match_tier"], "exact")
        self.assertEqual(finalized[1]["match_tier"], "close")
        self.assertEqual(finalized[2]["match_tier"], "related")

    def test_browse_uses_bayesian_rating_not_raw_rating(self):
        low_votes = {
            "id": "low-votes",
            "title": "One Vote",
            "average_rating": 10,
            "vote_count": 1,
        }
        established = {
            "id": "established",
            "title": "Many Votes",
            "average_rating": 8,
            "vote_count": 100,
        }

        self.assertEqual(
            [movie["id"] for movie in order_browse_movies([low_votes, established])],
            ["established", "low-votes"],
        )

    def test_similarity_candidates_get_a_score_reason_and_stable_order(self):
        first = {"id": "first", "title": "First Similar Movie"}
        second = {"id": "second", "title": "Second Similar Movie"}
        finalized = finalize_results(
            [
                {"movie": first, "percent": 0},
                {"movie": second, "percent": 0},
            ],
            {
                "first": {
                    "similarity_score": 72,
                    "similarity_source_title": "Zodiac",
                },
                "second": {
                    "similarity_score": 61,
                    "similarity_source_title": "Zodiac",
                },
            },
            self.preferences,
        )

        self.assertEqual([entry["movie"]["id"] for entry in finalized], ["first", "second"])
        self.assertEqual(finalized[0]["percent"], 72)
        self.assertEqual(finalized[0]["match_tier"], "close")
        self.assertIn("Zodiac", finalized[0]["match_reason"])
        self.assertTrue(finalized[0]["reasons"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
