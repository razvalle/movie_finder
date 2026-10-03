import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from poster_storage import insert_feedback, poster_public_base_url, poster_public_url, upload_poster
from data.upload_posters_supabase import _movie_poster_files
from app import movie_poster_url
from feedback_store import record_feedback


class SupabasePosterStorageTests(unittest.TestCase):
    def test_public_urls_use_supabase_bucket(self):
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co/",
            "SUPABASE_POSTER_BUCKET": "movie-posters",
        }, clear=False):
            self.assertEqual(
                poster_public_base_url(),
                "https://project.supabase.co/storage/v1/object/public/movie-posters",
            )
            self.assertEqual(
                poster_public_url(123),
                "https://project.supabase.co/storage/v1/object/public/movie-posters/123.jpg",
            )

    def test_uploader_targets_all_bundled_render_posters(self):
        files = _movie_poster_files()
        self.assertEqual(len(files), 1065)
        self.assertTrue(all(path.is_file() for path in files))

    def test_supabase_poster_precedes_legacy_tmdb_path(self):
        public_url = "https://project.supabase.co/storage/v1/object/public/movie-posters/42.jpg"
        with patch("app.POSTER_PUBLIC_BASE_URL", "https://project.supabase.co/storage/v1/object/public/movie-posters"):
            self.assertEqual(
                movie_poster_url({"poster_url": public_url, "poster_path": "/legacy.jpg"}),
                public_url,
            )

    def test_upload_uses_private_service_key_and_jpeg_content(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
            "SUPABASE_POSTER_BUCKET": "movie-posters",
        }, clear=False), patch("poster_storage.urlopen", return_value=Response()) as open_url:
            with patch.object(Path, "read_bytes", return_value=b"jpeg-data"):
                result = upload_poster(Path("42.jpg"))

        request = open_url.call_args.args[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.get_header("Content-type"), "image/jpeg")
        self.assertEqual(request.get_header("X-upsert"), "true")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertTrue(result.endswith("/42.jpg"))

    def test_feedback_uses_postgrest_with_service_key(self):
        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        feedback = {
            "query": "mystery",
            "movie_id": 42,
            "movie_title": "Example",
            "relevant": True,
            "created_at": "2026-10-01T00:00:00+00:00",
        }
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
        }, clear=False), patch("poster_storage.urlopen", return_value=Response()) as open_url:
            insert_feedback(feedback)

        request = open_url.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), feedback)
        self.assertTrue(request.full_url.endswith("/rest/v1/result_feedback"))

    def test_configured_feedback_store_uses_supabase(self):
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
        }, clear=False), patch("feedback_store.insert_feedback") as remote_insert:
            record_feedback("mystery", 42, "Example", True, Path("unused.sqlite3"))

        remote_insert.assert_called_once()
        self.assertEqual(remote_insert.call_args.args[0]["movie_id"], 42)
        self.assertTrue(remote_insert.call_args.args[0]["relevant"])


if __name__ == "__main__":
    unittest.main()