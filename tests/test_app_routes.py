"""Flask route regression checks for human search interactions.

Run from the project root:
    python tests/test_app_routes.py
"""

import sys
import unittest
import re
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

    def test_query_genre_overrides_stale_dropdown(self):
        response = self.client.get("/?q=japanese+horror+movies&genre=action&per_page=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"The genre in your search overrides", response.data)
        self.assertNotIn(b'value="action" selected', response.data)

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

    def test_debug_panel_shows_structured_interpretation(self):
        response = self.client.get(
            "/?q=whats+the+best+ph+horror+movies+from+2020&debug=1&per_page=12"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Query Debug", response.data)
        self.assertIn(b"what is the best philippines horror movies from 2020", response.data)
        self.assertIn(b"Philippines", response.data)
        self.assertIn(b"Detected intent</dt><dd>best", response.data)

    def test_query_year_is_applied_before_results_are_rendered(self):
        response = self.client.get("/?q=movies+from+2015+to+2020&per_page=48")
        self.assertEqual(response.status_code, 200)
        years = [int(value) for value in re.findall(rb"\xc2\xb7 (\d{4})</div>", response.data)]
        self.assertTrue(years)
        self.assertTrue(all(2015 <= year <= 2020 for year in years))

    def test_debug_panel_shows_negative_filters(self):
        response = self.client.get("/?q=filipino+horror+movies+without+comedy&debug=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Negative filters", response.data)
        self.assertIn(b"Comedy", response.data)


if __name__ == "__main__":
    unittest.main()
