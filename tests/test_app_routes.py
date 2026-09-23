"""Flask route regression checks for human search interactions.

Run from the project root:
    python tests/test_app_routes.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import COUNTRY_LABELS, app, detect_nationality
from data.movies import MOVIES


class SearchRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_all_available_country_names_resolve(self):
        codes = sorted({
            code
            for movie in MOVIES
            for code in movie.get("origin_regions", [])
            if code in COUNTRY_LABELS
        })
        failures = [
            (code, COUNTRY_LABELS[code], detect_nationality(f"movies from {COUNTRY_LABELS[code]}"))
            for code in codes
            if detect_nationality(f"movies from {COUNTRY_LABELS[code]}") != code
        ]
        self.assertEqual(failures, [])

    def test_title_overrides_stale_filters(self):
        response = self.client.get(
            "/?q=recommend+the+grand+budapest+hotel&genre=action&nationality=CA&per_page=12"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"The Grand Budapest Hotel", response.data)

    def test_country_overrides_stale_origin(self):
        response = self.client.get("/?q=movies+from+cabo+verde&nationality=IN&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="CV" selected', response.data)
        self.assertNotIn(b'value="IN" selected', response.data)

    def test_country_genre_and_human_input_routes(self):
        cases = [
            ("/?q=japanese+horror+movies&genre=action&nationality=IN&per_page=12", b'value="JP" selected'),
            ("/?q=a+detective+solving+a+murder+in+a+mansion&per_page=12", b"Recommended Movies"),
            ("/?q=scarry+rom+com+but+no+sad+ending+under+two+hours&per_page=12", b"Recommended Movies"),
            ("/?q=recommend+a+canadian+movie+called+incendies&per_page=12", b"Incendies"),
            ("/?q=zzzzunknownword&per_page=12", b"No movies found."),
        ]
        for url, marker in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.data)


if __name__ == "__main__":
    unittest.main()
