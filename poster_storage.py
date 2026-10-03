import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_POSTER_BUCKET = "movie-posters"


def _config():
    project_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.environ.get("SUPABASE_POSTER_BUCKET", DEFAULT_POSTER_BUCKET).strip()
    return project_url, service_key, bucket


def supabase_is_configured():
    project_url, service_key, _ = _config()
    return bool(project_url and service_key)


def poster_public_base_url():
    project_url, _, bucket = _config()
    if not project_url or not bucket:
        return ""
    return f"{project_url}/storage/v1/object/public/{quote(bucket, safe='')}"


def poster_public_url(movie_id):
    base_url = poster_public_base_url()
    return f"{base_url}/{quote(str(movie_id), safe='')}.jpg" if base_url else ""


def _auth_headers(service_key):
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }


def upload_poster(path, bucket=None):
    project_url, service_key, configured_bucket = _config()
    if not project_url or not service_key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY before uploading posters.")
    bucket = bucket or configured_bucket
    object_name = quote(path.name, safe="")
    url = f"{project_url}/storage/v1/object/{quote(bucket, safe='')}/{object_name}"
    headers = {
        **_auth_headers(service_key),
        "Content-Type": "image/jpeg",
        "Cache-Control": "public, max-age=31536000, immutable",
        "x-upsert": "true",
    }
    request = Request(url, data=path.read_bytes(), headers=headers, method="PUT")
    with urlopen(request, timeout=120) as response:
        if response.status not in {200, 201}:
            raise RuntimeError(f"Supabase upload failed with HTTP {response.status}.")
    return poster_public_url(path.stem)


def verify_public_poster(url, expected_size=None):
    request = Request(url, method="HEAD")
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            return False
        content_length = response.headers.get("Content-Length")
        return expected_size is None or content_length is None or int(content_length) == expected_size


def insert_feedback(feedback):
    project_url, service_key, _ = _config()
    if not project_url or not service_key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY before writing remote feedback.")
    url = f"{project_url}/rest/v1/result_feedback"
    headers = {
        **_auth_headers(service_key),
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    request = Request(url, data=json.dumps(feedback).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            if response.status not in {200, 201, 204}:
                raise RuntimeError(f"Supabase feedback insert failed with HTTP {response.status}.")
    except HTTPError as error:
        raise RuntimeError(f"Supabase feedback insert failed with HTTP {error.code}.") from error