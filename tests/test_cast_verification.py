import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlp.cast_verification import classify_cast_gender, evaluate_cast_constraint


class CastVerificationTests(unittest.TestCase):
    def test_strong_all_male_cast_is_exact_match(self):
        movie = {
            "title": "The Last Stand",
            "cast": [
                {"name": "Alec", "character": "General", "gender": 2},
                {"name": "Boris", "character": "Soldier", "gender": 2},
                {"name": "Dale", "character": "Pilot", "gender": 2},
                {"name": "Evan", "character": "Medic", "gender": 2},
                {"name": "Felix", "character": "Driver", "gender": 2},
            ],
            "overview": "A group of soldiers enters a town.",
        }
        result = classify_cast_gender(movie)
        self.assertEqual(result["status"], "strong")
        self.assertFalse(result["contradicted"])

    def test_partial_gender_coverage_is_closest_match(self):
        movie = {
            "title": "The Uncertain Unit",
            "cast": [
                {"name": "Alec", "character": "General", "gender": 2},
                {"name": "Boris", "character": "Soldier", "gender": 2},
                {"name": "Cory", "character": "Driver", "gender": 2},
                {"name": "Dana", "character": "Pilot", "gender": 2},
                {"name": "Evan", "character": "Medic", "gender": 0},
            ],
            "overview": "A group of soldiers enters a town.",
        }
        self.assertEqual(classify_cast_gender(movie)["status"], "moderate")
        self.assertTrue(evaluate_cast_constraint(movie, {"type": "cast_gender", "require": "all_male"}))

    def test_unknown_coverage_is_unverified(self):
        movie = {
            "title": "Untold Story",
            "cast": [
                {"name": "Alex", "character": "Narrator", "gender": 2},
                {"name": "Sam", "character": "Guide", "gender": 0},
            ],
            "overview": "A man narrates a documentary about a journey.",
        }
        result = classify_cast_gender(movie)
        self.assertEqual(result["status"], "weak")
        self.assertIsInstance(result["reason"], str)

    def test_narrative_female_cue_contradicts_even_without_cast(self):
        result = classify_cast_gender({"overview": "A woman leads a crew across the ocean."})
        self.assertEqual(result["status"], "contradicted")

    def test_no_women_query_excludes_contradicted_titles(self):
        movie = {
            "title": "Annabelle's March",
            "cast": [
                {"name": "Annabelle", "character": "Lead", "gender": 1},
                {"name": "Marta", "character": "Sister", "gender": 1},
            ],
            "overview": "A woman leads a family team through a crisis.",
        }
        self.assertFalse(evaluate_cast_constraint(movie, {"type": "cast_gender", "exclude": "female"}))

    def test_no_women_query_keeps_unverified_as_closest_candidate(self):
        self.assertTrue(evaluate_cast_constraint(
            {"title": "Unverified", "cast": []},
            {"type": "cast_gender", "exclude": "female"},
        ))

    def test_female_lead_constraint_uses_top_billed_cast(self):
        movie = {
            "cast": [
                {"name": "Avery", "character": "Lead", "gender": 1},
                {"name": "Blake", "character": "Detective", "gender": 2},
                {"name": "Casey", "character": "Witness", "gender": 2},
            ],
        }
        self.assertTrue(evaluate_cast_constraint(
            movie,
            {"type": "cast_gender", "require": "female_lead"},
        ))

    def test_all_female_constraint_accepts_female_evidence(self):
        movie = {
            "cast": [
                {"name": f"Actor {index}", "character": "Team member", "gender": 1}
                for index in range(5)
            ],
        }
        self.assertTrue(evaluate_cast_constraint(
            movie,
            {"type": "cast_gender", "require": "all_female"},
        ))


if __name__ == "__main__":
    unittest.main()
