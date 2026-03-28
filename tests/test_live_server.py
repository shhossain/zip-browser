"""
Live web-server tests.

These tests start the Flask app on a real HTTP port and make requests via
``urllib.request`` (no extra dependencies needed). They exercise the full
HTTP stack including WSGI, routing, session cookies, and CSRF.

Run with:
    pytest tests/test_live_server.py -v

The tests use a random free port so they don't conflict with anything.
"""
import io
import json
import re
import socket
import threading
import time
import zipfile
import urllib.request
import urllib.parse
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from src.config import Config
from src.auth import AuthManager
from src.user_manager import UserManager
from src.zip_manager import ZipManager
from src.utils import get_source_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port():
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _extract_csrf_token(html: str) -> str | None:
    """Extract CSRF token from HTML (hidden input)."""
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if m:
        return m.group(1)
    # Also try the meta tag variant
    m = re.search(r'<meta[^>]*name="csrf-token"[^>]*content="([^"]+)"', html)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_zip(tmp_path_factory):
    """Create a sample ZIP for the live server tests."""
    base = tmp_path_factory.mktemp("live")
    zip_path = base / "live_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("hello.txt", "Hello from live server test!")
        zf.writestr("folder/nested.txt", "Nested content")
        zf.writestr("images/tiny.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    return str(zip_path)


@pytest.fixture(scope="module")
def live_user_manager(tmp_path_factory):
    """Isolated UserManager for the live server."""
    base = tmp_path_factory.mktemp("live_users")
    app_dir = base / ".zip-browser"
    app_dir.mkdir()

    class _M(UserManager):
        def _get_users_file_path(self):
            return app_dir / "users.json"

    mgr = _M()
    mgr.create_user("liveuser", "livepass", is_admin=True)
    return mgr


@pytest.fixture(scope="module")
def live_server(live_zip, live_user_manager):
    """
    Start a real Flask server in a background thread.
    Yields (host, port) tuple.
    """
    from flask import Flask
    from flask_login import LoginManager
    from flask_wtf.csrf import CSRFProtect, generate_csrf
    from src.routes import (
        create_auth_routes,
        create_browse_routes,
        create_video_routes,
        create_search_routes,
    )
    from src.utils import get_file_icon

    port = _free_port()
    config = Config(zip_paths=[live_zip], host="127.0.0.1", port=port,
                    debug=False, multiuser=True)

    app = Flask(__name__,
                template_folder=str(Path(__file__).resolve().parent.parent / "src" / "templates"),
                static_folder=str(Path(__file__).resolve().parent.parent / "src" / "static"))
    app.secret_key = config.secret_key

    csrf = CSRFProtect(app)

    am = AuthManager(live_user_manager)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(uid):
        return am.load_user(uid)

    @app.context_processor
    def ctx():
        return dict(get_file_icon=get_file_icon)

    app.jinja_env.globals["csrf_token"] = generate_csrf

    zm = ZipManager()
    zm.initialize_zip_files([live_zip])

    app.register_blueprint(create_auth_routes(am))
    app.register_blueprint(create_browse_routes(zm))
    app.register_blueprint(create_video_routes(zm))
    app.register_blueprint(create_search_routes(zm))

    app.config["APP_CONFIG"] = config

    # Run in a daemon thread so it doesn't outlive the test session
    server_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, use_reloader=False),
        daemon=True,
    )
    server_thread.start()

    # Wait for the server to start accepting connections
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base_url}/login", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    yield base_url, live_zip

    # Server thread is daemon – Python will clean it up on exit.


@pytest.fixture()
def http_client():
    """HTTP client with cookie support (session tracking)."""
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    return opener


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLiveServerBasic:
    """Verify the server boots and responds to basic requests."""

    def test_server_is_up(self, live_server, http_client):
        base_url, _ = live_server
        resp = http_client.open(f"{base_url}/login")
        assert resp.status == 200
        html = resp.read().decode()
        assert "Login" in html or "login" in html

    def test_root_redirects_to_login(self, live_server, http_client):
        base_url, _ = live_server
        # Follow redirects manually to verify chain
        req = urllib.request.Request(f"{base_url}/")
        try:
            resp = http_client.open(req)
            # If we land here, redirects were followed
            html = resp.read().decode()
            assert "login" in html.lower() or "zip" in html.lower()
        except urllib.error.HTTPError as e:
            assert e.code in (302, 308)


class TestLiveServerAuth:
    """Login / logout over real HTTP with CSRF tokens."""

    def _login(self, base_url, opener, username="liveuser", password="livepass"):
        """Perform login flow: GET login page -> extract CSRF -> POST."""
        resp = opener.open(f"{base_url}/login")
        html = resp.read().decode()
        csrf = _extract_csrf_token(html)

        data = urllib.parse.urlencode({
            "username": username,
            "password": password,
            **({"csrf_token": csrf} if csrf else {}),
        }).encode()
        req = urllib.request.Request(f"{base_url}/login", data=data, method="POST")
        return opener.open(req)

    def test_login_success(self, live_server, http_client):
        base_url, _ = live_server
        resp = self._login(base_url, http_client)
        assert resp.status == 200
        html = resp.read().decode()
        # After login we should see the zip list or the user's name
        assert "liveuser" in html.lower() or "zip" in html.lower() or "live_test" in html.lower()

    def test_login_failure(self, live_server, http_client):
        base_url, _ = live_server
        try:
            resp = self._login(base_url, http_client, password="wrongpassword")
            html = resp.read().decode()
            assert "invalid" in html.lower() or "login" in html.lower()
        except urllib.error.HTTPError:
            pass  # Server may return 4xx on bad login

    def test_logout(self, live_server, http_client):
        base_url, _ = live_server
        self._login(base_url, http_client)
        resp = http_client.open(f"{base_url}/logout")
        html = resp.read().decode()
        assert "login" in html.lower()


class TestLiveServerBrowse:
    """Browsing ZIP contents over real HTTP."""

    def _login(self, base_url, opener):
        resp = opener.open(f"{base_url}/login")
        html = resp.read().decode()
        csrf = _extract_csrf_token(html)
        data = urllib.parse.urlencode({
            "username": "liveuser",
            "password": "livepass",
            **({"csrf_token": csrf} if csrf else {}),
        }).encode()
        opener.open(urllib.request.Request(f"{base_url}/login", data=data, method="POST"))

    def test_zip_list(self, live_server, http_client):
        base_url, _ = live_server
        self._login(base_url, http_client)
        resp = http_client.open(f"{base_url}/zips")
        assert resp.status == 200
        html = resp.read().decode()
        assert "live_test.zip" in html

    def test_browse_root(self, live_server, http_client):
        base_url, live_zip = live_server
        self._login(base_url, http_client)
        zip_id = get_source_hash(live_zip)
        resp = http_client.open(f"{base_url}/browse/{zip_id}/")
        assert resp.status == 200
        html = resp.read().decode()
        assert "hello.txt" in html

    def test_browse_subfolder(self, live_server, http_client):
        base_url, live_zip = live_server
        self._login(base_url, http_client)
        zip_id = get_source_hash(live_zip)
        resp = http_client.open(f"{base_url}/browse/{zip_id}/folder")
        assert resp.status == 200
        html = resp.read().decode()
        assert "nested.txt" in html

    def test_view_file(self, live_server, http_client):
        base_url, live_zip = live_server
        self._login(base_url, http_client)
        zip_id = get_source_hash(live_zip)
        resp = http_client.open(f"{base_url}/view/{zip_id}/hello.txt")
        assert resp.status == 200
        body = resp.read()
        assert b"Hello from live server test!" in body

    def test_browse_nonexistent_returns_404(self, live_server, http_client):
        base_url, _ = live_server
        self._login(base_url, http_client)
        try:
            http_client.open(f"{base_url}/browse/fakezip/")
            assert False, "Expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404


class TestLiveServerSearch:
    """Search functionality over real HTTP."""

    def _login(self, base_url, opener):
        resp = opener.open(f"{base_url}/login")
        html = resp.read().decode()
        csrf = _extract_csrf_token(html)
        data = urllib.parse.urlencode({
            "username": "liveuser",
            "password": "livepass",
            **({"csrf_token": csrf} if csrf else {}),
        }).encode()
        opener.open(urllib.request.Request(f"{base_url}/login", data=data, method="POST"))

    def test_search_returns_results(self, live_server, http_client):
        base_url, live_zip = live_server
        self._login(base_url, http_client)
        zip_id = get_source_hash(live_zip)
        resp = http_client.open(f"{base_url}/search/{zip_id}?q=hello")
        assert resp.status == 200
        html = resp.read().decode()
        assert "hello" in html.lower()

    def test_search_empty_query(self, live_server, http_client):
        base_url, live_zip = live_server
        self._login(base_url, http_client)
        zip_id = get_source_hash(live_zip)
        resp = http_client.open(f"{base_url}/search/{zip_id}?q=")
        assert resp.status == 200

    def test_search_no_results(self, live_server, http_client):
        base_url, live_zip = live_server
        self._login(base_url, http_client)
        zip_id = get_source_hash(live_zip)
        resp = http_client.open(f"{base_url}/search/{zip_id}?q=zzzzzznonexistent")
        assert resp.status == 200


class TestLiveServerProtection:
    """Verify auth protection and security headers."""

    def test_unauthenticated_browse_redirects(self, live_server):
        """Accessing protected routes without login should redirect."""
        base_url, live_zip = live_server
        zip_id = get_source_hash(live_zip)
        # Fresh client without cookies
        try:
            resp = urllib.request.urlopen(f"{base_url}/browse/{zip_id}/")
            # If redirect was followed, we should land on login
            html = resp.read().decode()
            assert "login" in html.lower()
        except urllib.error.HTTPError as e:
            assert e.code in (302, 401, 403)

    def test_unauthenticated_zips_redirects(self, live_server):
        base_url, _ = live_server
        try:
            resp = urllib.request.urlopen(f"{base_url}/zips")
            html = resp.read().decode()
            assert "login" in html.lower()
        except urllib.error.HTTPError as e:
            assert e.code in (302, 401, 403)
