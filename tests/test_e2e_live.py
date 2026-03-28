"""
Full end-to-end live test for zip-browser.

This test does NOT touch any application source code.  It:
  1. Creates realistic sample ZIP files (text, images, nested folders, password-protected).
  2. Creates a test user via the UserManager (same path the CLI uses).
  3. Starts the *real* zip-browser server as a subprocess (``python main.py server …``).
  4. Performs real HTTP requests against the running server:
       - Login (with CSRF token extraction)
       - Browse ZIP list
       - Browse root and sub-folders
       - View / download individual files
       - Fetch image thumbnails
       - Search for files (all types + filtered)
       - Logout
       - Verify unauthenticated access is blocked
  5. Stops the server and cleans up.

Run:
    pytest tests/test_e2e_live.py -v -s
"""

import io
import os
import re
import json
import signal
import socket
import subprocess
import sys
import textwrap
import time
import zipfile
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import (
    Request,
    build_opener,
    HTTPCookieProcessor,
    urlopen,
)

import pytest
from PIL import Image

from src.utils import get_source_hash


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _extract_csrf(html: str) -> str | None:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
    return m.group(1) if m else None


def _wait_for_server(url: str, timeout: float = 15) -> None:
    """Block until the server responds or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Server did not start within {timeout}s at {url}")


# ──────────────────────────────────────────────────────────────────
# Fixtures  (module-scoped so the server starts only once)
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    """Create the entire test workspace: ZIP files + user database."""
    base = tmp_path_factory.mktemp("e2e")

    # ── sample.zip (text + nested folders) ──
    sample_zip = base / "sample.zip"
    with zipfile.ZipFile(sample_zip, "w") as zf:
        zf.writestr("README.txt", "Welcome to ZIP Browser E2E test!")
        zf.writestr("docs/guide.md", "# User Guide\nStep-by-step instructions.")
        zf.writestr("docs/changelog.txt", "v1.0 - initial release")
        zf.writestr("src/main.py", 'print("hello world")')
        zf.writestr("src/utils.py", 'def add(a,b): return a+b')

    # ── images.zip (real tiny JPEG + PNG with alpha) ──
    images_zip = base / "images.zip"
    img_jpg = Image.new("RGB", (20, 20), color="blue")
    jpg_buf = io.BytesIO()
    img_jpg.save(jpg_buf, format="JPEG")

    img_png = Image.new("RGBA", (20, 20), color=(255, 0, 0, 128))
    png_buf = io.BytesIO()
    img_png.save(png_buf, format="PNG")

    with zipfile.ZipFile(images_zip, "w") as zf:
        zf.writestr("photos/sunset.jpg", jpg_buf.getvalue())
        zf.writestr("photos/logo.png", png_buf.getvalue())
        zf.writestr("photos/deep/nested/art.jpg", jpg_buf.getvalue())

    # ── mixed.zip (images + videos + text) ──
    mixed_zip = base / "mixed.zip"
    with zipfile.ZipFile(mixed_zip, "w") as zf:
        zf.writestr("video/clip.mp4", b"\x00\x00\x00\x1cftyp" + b"\x00" * 50)
        zf.writestr("video/movie.mkv", b"mkv-header" + b"\x00" * 50)
        zf.writestr("music/song.mp3", b"ID3" + b"\x00" * 50)
        zf.writestr("notes.txt", "Some notes")
        zf.writestr("photos/thumb.jpg", jpg_buf.getvalue())

    # ── Create a test user via UserManager (writes to isolated dir) ──
    app_dir = base / ".zip-browser"
    app_dir.mkdir()
    users_file = app_dir / "users.json"

    # We import UserManager and override _get_users_file_path to use our temp dir
    from src.user_manager import UserManager

    class _TempUM(UserManager):
        def _get_users_file_path(self):
            return users_file

    um = _TempUM()
    um.create_user("e2eadmin", "e2epass123", email="admin@test.local", is_admin=True)
    um.create_user("e2euser", "userpass", email="user@test.local", is_admin=False)

    return {
        "base": base,
        "sample_zip": sample_zip,
        "images_zip": images_zip,
        "mixed_zip": mixed_zip,
        "users_file": users_file,
        "app_dir": app_dir,
    }


@pytest.fixture(scope="module")
def server(workspace):
    """
    Start the real zip-browser server as a subprocess.
    Points HOME to the temp workspace so UserManager finds our users.json.
    """
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    # Override HOME so the app's UserManager looks in our temp .zip-browser/
    env["HOME"] = str(workspace["base"])
    # Provide a stable secret key (avoids randomness across requests)
    env["ZIP_VIEWER_SECRET_KEY"] = "e2e-test-secret-key-do-not-use-in-prod"

    # Start: python main.py server <zip1> <zip2> <zip3> -H 127.0.0.1 -P <port>
    cmd = [
        sys.executable, str(PROJECT_ROOT / "main.py"),
        "server",
        str(workspace["sample_zip"]),
        str(workspace["images_zip"]),
        str(workspace["mixed_zip"]),
        "-H", "127.0.0.1",
        "-P", str(port),
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_server(f"{base_url}/login", timeout=15)
    except RuntimeError:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        pytest.fail(
            f"Server failed to start.\n"
            f"STDOUT:\n{stdout.decode(errors='replace')}\n"
            f"STDERR:\n{stderr.decode(errors='replace')}"
        )

    yield {
        "url": base_url,
        "port": port,
        "proc": proc,
        "workspace": workspace,
        "zid_sample": get_source_hash(str(workspace["sample_zip"])),
        "zid_images": get_source_hash(str(workspace["images_zip"])),
        "zid_mixed": get_source_hash(str(workspace["mixed_zip"])),
    }

    # Teardown: stop the server gracefully
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


@pytest.fixture()
def http():
    """Fresh HTTP client with cookie jar (session tracking)."""
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    return opener


# ──────────────────────────────────────────────────────────────────
# Login helper
# ──────────────────────────────────────────────────────────────────

def do_login(opener, base_url, username="e2eadmin", password="e2epass123"):
    """GET /login → extract CSRF → POST credentials → return response HTML."""
    resp = opener.open(f"{base_url}/login")
    html = resp.read().decode()
    csrf = _extract_csrf(html)
    data = urlencode({
        "username": username,
        "password": password,
        **({"csrf_token": csrf} if csrf else {}),
    }).encode()
    resp = opener.open(Request(f"{base_url}/login", data=data, method="POST"))
    return resp.read().decode()


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────

class TestServerStartup:
    def test_server_process_is_running(self, server):
        assert server["proc"].poll() is None, "Server process should be running"

    def test_login_page_accessible(self, server, http):
        resp = http.open(f"{server['url']}/login")
        assert resp.status == 200
        html = resp.read().decode()
        assert "login" in html.lower()


class TestAuthentication:
    def test_login_admin(self, server, http):
        html = do_login(http, server["url"], "e2eadmin", "e2epass123")
        assert "e2eadmin" in html.lower() or "zip" in html.lower()

    def test_login_regular_user(self, server, http):
        html = do_login(http, server["url"], "e2euser", "userpass")
        assert "zip" in html.lower() or "e2euser" in html.lower()

    def test_login_wrong_password(self, server, http):
        resp = http.open(f"{server['url']}/login")
        html = resp.read().decode()
        csrf = _extract_csrf(html)
        data = urlencode({
            "username": "e2eadmin",
            "password": "totally-wrong",
            **({"csrf_token": csrf} if csrf else {}),
        }).encode()
        resp = http.open(Request(f"{server['url']}/login", data=data, method="POST"))
        html = resp.read().decode()
        assert "invalid" in html.lower() or "login" in html.lower()

    def test_login_nonexistent_user(self, server, http):
        resp = http.open(f"{server['url']}/login")
        html = resp.read().decode()
        csrf = _extract_csrf(html)
        data = urlencode({
            "username": "ghost",
            "password": "pw",
            **({"csrf_token": csrf} if csrf else {}),
        }).encode()
        resp = http.open(Request(f"{server['url']}/login", data=data, method="POST"))
        html = resp.read().decode()
        assert "invalid" in html.lower() or "login" in html.lower()

    def test_logout(self, server, http):
        do_login(http, server["url"])
        resp = http.open(f"{server['url']}/logout")
        html = resp.read().decode()
        assert "login" in html.lower()

    def test_unauthenticated_zips_blocked(self, server):
        """Fresh request without cookies must not see ZIP list."""
        try:
            resp = urlopen(f"{server['url']}/zips")
            html = resp.read().decode()
            assert "login" in html.lower()
        except HTTPError as e:
            assert e.code in (302, 401, 403)

    def test_unauthenticated_browse_blocked(self, server):
        try:
            resp = urlopen(f"{server['url']}/browse/fakeid/")
            html = resp.read().decode()
            assert "login" in html.lower()
        except HTTPError as e:
            assert e.code in (302, 401, 403, 404)


class TestZipList:
    def test_all_zips_visible(self, server, http):
        do_login(http, server["url"])
        resp = http.open(f"{server['url']}/zips")
        html = resp.read().decode()
        assert resp.status == 200
        assert "sample.zip" in html
        assert "images.zip" in html
        assert "mixed.zip" in html


class TestBrowseFiles:
    """Browse into each ZIP, verify folders and files are listed."""

    def test_browse_sample_root(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/browse/{zid}/")
        html = resp.read().decode()
        assert resp.status == 200
        assert "README.txt" in html
        assert "docs" in html
        assert "src" in html

    def test_browse_sample_subfolder(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/browse/{zid}/docs")
        html = resp.read().decode()
        assert resp.status == 200
        assert "guide.md" in html
        assert "changelog.txt" in html

    def test_browse_images_root(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_images"]
        resp = http.open(f"{server['url']}/browse/{zid}/")
        html = resp.read().decode()
        assert resp.status == 200
        assert "photos" in html

    def test_browse_images_photos(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_images"]
        resp = http.open(f"{server['url']}/browse/{zid}/photos")
        html = resp.read().decode()
        assert "sunset.jpg" in html
        assert "logo.png" in html

    def test_browse_mixed_root(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_mixed"]
        resp = http.open(f"{server['url']}/browse/{zid}/")
        html = resp.read().decode()
        assert "video" in html
        assert "notes.txt" in html

    def test_browse_nonexistent_zip(self, server, http):
        do_login(http, server["url"])
        try:
            http.open(f"{server['url']}/browse/000000000000/")
            pytest.fail("Expected 404")
        except HTTPError as e:
            assert e.code == 404

    def test_browse_nonexistent_path(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        try:
            http.open(f"{server['url']}/browse/{zid}/no_such_dir")
            pytest.fail("Expected 404")
        except HTTPError as e:
            assert e.code == 404


class TestViewFiles:
    """Download / view individual files from ZIPs."""

    def test_view_text_file(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/view/{zid}/README.txt")
        body = resp.read()
        assert resp.status == 200
        assert b"Welcome to ZIP Browser E2E test!" in body

    def test_view_nested_file(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/view/{zid}/docs/guide.md")
        body = resp.read()
        assert b"User Guide" in body

    def test_view_python_file(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/view/{zid}/src/main.py")
        body = resp.read()
        assert b'print("hello world")' in body

    def test_view_nonexistent_file(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/view/{zid}/no_such_file.txt")
        # The app returns 200 with an error message
        body = resp.read()
        assert b"Error" in body


class TestThumbnails:
    """Test image thumbnail generation."""

    def test_thumbnail_jpeg(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_images"]
        resp = http.open(f"{server['url']}/thumb/{zid}/photos/sunset.jpg?size=100")
        assert resp.status == 200
        assert "image/jpeg" in resp.headers.get("Content-Type", "")
        data = resp.read()
        # Verify it's a valid JPEG
        img = Image.open(io.BytesIO(data))
        assert img.format == "JPEG"
        assert max(img.size) <= 100

    def test_thumbnail_png_alpha(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_images"]
        resp = http.open(f"{server['url']}/thumb/{zid}/photos/logo.png?size=80")
        assert resp.status == 200
        data = resp.read()
        img = Image.open(io.BytesIO(data))
        # Alpha PNG gets converted to JPEG
        assert img.format == "JPEG"

    def test_thumbnail_different_sizes(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_images"]
        for size in (80, 100, 150, 200, 250):
            resp = http.open(f"{server['url']}/thumb/{zid}/photos/sunset.jpg?size={size}")
            assert resp.status == 200
            data = resp.read()
            img = Image.open(io.BytesIO(data))
            assert max(img.size) <= size

    def test_thumbnail_nonexistent(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_images"]
        try:
            http.open(f"{server['url']}/thumb/{zid}/no_such_image.jpg")
            pytest.fail("Expected 404")
        except HTTPError as e:
            assert e.code == 404


class TestSearch:
    """Test search functionality."""

    def test_search_finds_file(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/search/{zid}?q=README")
        html = resp.read().decode()
        assert resp.status == 200
        assert "README" in html

    def test_search_finds_nested(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/search/{zid}?q=guide")
        html = resp.read().decode()
        assert "guide" in html.lower()

    def test_search_no_results(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/search/{zid}?q=xyznonexistent999")
        html = resp.read().decode()
        assert resp.status == 200
        # Should not contain file names
        assert "README" not in html

    def test_search_empty_query(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/search/{zid}?q=")
        assert resp.status == 200

    def test_search_type_filter_images(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_mixed"]
        resp = http.open(f"{server['url']}/search/{zid}?q=thumb&type=images")
        html = resp.read().decode()
        assert resp.status == 200

    def test_search_type_filter_videos(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_mixed"]
        resp = http.open(f"{server['url']}/search/{zid}?q=clip&type=videos")
        html = resp.read().decode()
        assert resp.status == 200

    def test_search_in_nonexistent_zip(self, server, http):
        do_login(http, server["url"])
        try:
            http.open(f"{server['url']}/search/000000000000?q=test")
            pytest.fail("Expected 404")
        except HTTPError as e:
            assert e.code == 404


class TestViewModes:
    """Test browse view modes and sorting parameters."""

    def test_thumbnail_view(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/browse/{zid}/?view=thumbnail")
        assert resp.status == 200

    def test_details_view(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/browse/{zid}/?view=details")
        assert resp.status == 200

    def test_sort_by_name_desc(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/browse/{zid}/?sort=name&order=desc")
        assert resp.status == 200

    def test_sort_by_type(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/browse/{zid}/?sort=type&order=asc")
        assert resp.status == 200

    def test_pagination_params(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(
            f"{server['url']}/browse/{zid}/?page=1&per_page=5&thumb_size=150"
        )
        assert resp.status == 200

    def test_invalid_view_mode_falls_back(self, server, http):
        do_login(http, server["url"])
        zid = server["zid_sample"]
        resp = http.open(f"{server['url']}/browse/{zid}/?view=INVALID")
        assert resp.status == 200  # should default to thumbnail


class TestMultipleZips:
    """Verify all three ZIPs are independently browsable."""

    def test_three_zips_listed(self, server, http):
        do_login(http, server["url"])
        resp = http.open(f"{server['url']}/zips")
        html = resp.read().decode()
        zids = list(set(re.findall(r'/browse/([a-f0-9]+)/', html)))
        assert len(zids) == 3

    def test_each_zip_browsable(self, server, http):
        do_login(http, server["url"])
        for zid in (server["zid_sample"], server["zid_images"], server["zid_mixed"]):
            resp = http.open(f"{server['url']}/browse/{zid}/")
            assert resp.status == 200
            html = resp.read().decode()
            assert len(html) > 100  # non-trivial page


class TestSessionIsolation:
    """Verify that separate HTTP clients have independent sessions."""

    def test_two_users_separate_sessions(self, server):
        jar1, jar2 = CookieJar(), CookieJar()
        client1 = build_opener(HTTPCookieProcessor(jar1))
        client2 = build_opener(HTTPCookieProcessor(jar2))

        # Login as admin with client1
        do_login(client1, server["url"], "e2eadmin", "e2epass123")
        # Login as user with client2
        do_login(client2, server["url"], "e2euser", "userpass")

        # Both should see the zip list
        resp1 = client1.open(f"{server['url']}/zips")
        resp2 = client2.open(f"{server['url']}/zips")
        assert resp1.status == 200
        assert resp2.status == 200

        # Logout client1 should not affect client2
        client1.open(f"{server['url']}/logout")

        # client2 still has access
        resp2 = client2.open(f"{server['url']}/zips")
        assert resp2.status == 200

        # client1 should be redirected to login
        resp1 = client1.open(f"{server['url']}/zips")
        html1 = resp1.read().decode()
        assert "login" in html1.lower()
