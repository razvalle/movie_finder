"""A missing TMDB key must leave source and progress files untouched."""

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import enrich_tmdb


class TmdbNoKeyTests(unittest.TestCase):
    def test_enrichment_is_a_noop_without_key(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = root / "catalog.json"
            progress = root / "progress.json"
            catalog.write_text('[{"title":"Unchanged"}]', encoding="utf-8")
            progress.write_text('[{"title":"Progress"}]', encoding="utf-8")
            before = (catalog.read_bytes(), progress.read_bytes())
            output = StringIO()
            with patch.dict("os.environ", {}, clear=True), \
                    patch.object(enrich_tmdb, "CATALOG_PATH", catalog), \
                    patch.object(enrich_tmdb, "TEMP_PATH", progress), \
                    patch.object(enrich_tmdb, "request_json", side_effect=AssertionError("network must not be called")) as request_json, \
                    redirect_stdout(output):
                self.assertEqual(enrich_tmdb.enrich_catalog(), 0)
            request_json.assert_not_called()
            self.assertEqual((catalog.read_bytes(), progress.read_bytes()), before)
            self.assertIn("catalog unchanged", output.getvalue())


if __name__ == "__main__":
    unittest.main()