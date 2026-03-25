"""
Shared fixtures for ZIP Browser tests.
"""
import os
import io
import json
import shutil
import tempfile
import zipfile

import pytest

from src.config import Config
from src.user_manager import UserManager
from src.auth import AuthManager
from src.zip_manager import ZipManager


# ---------------------------------------------------------------------------
# Temporary directories & files
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_dir(tmp_path):
    """Provide a temporary directory that is cleaned up automatically."""
    return tmp_path


@pytest.fixture()
def sample_zip(tmp_path):
    """Create a simple ZIP file with text + image files for testing."""
    zip_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "Hello World")
        zf.writestr("docs/guide.md", "# Guide\nSome content")
        zf.writestr("images/photo.jpg", b"\xff\xd8\xff\xe0dummy-jpeg")
        zf.writestr("images/logo.png", b"\x89PNGdummy-png")
        zf.writestr("videos/clip.mp4", b"\x00\x00\x00\x1cftyp")
        zf.writestr("videos/movie.mkv", b"mkv-dummy")
        zf.writestr(".__hidden", "should be filtered")
        zf.writestr("__MACOSX/resource", "should be filtered")
        zf.writestr("Thumbs.db", "should be filtered")
    return str(zip_path)


@pytest.fixture()
def password_zip(tmp_path):
    """Create a password-protected ZIP file (standard encryption)."""
    import pyzipper
    zip_path = tmp_path / "protected.zip"
    with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED,
                              encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(b"secret123")
        zf.writestr("secret.txt", "Top Secret Content")
    return str(zip_path)


@pytest.fixture()
def image_zip(tmp_path):
    """Create a ZIP with a real JPEG thumbnail for image route testing."""
    from PIL import Image
    zip_path = tmp_path / "images.zip"

    # Create a tiny real JPEG in memory
    img = Image.new("RGB", (10, 10), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    # Create a tiny real PNG with alpha
    img_rgba = Image.new("RGBA", (10, 10), color=(0, 255, 0, 128))
    buf2 = io.BytesIO()
    img_rgba.save(buf2, format="PNG")
    png_bytes = buf2.getvalue()

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.jpg", jpeg_bytes)
        zf.writestr("alpha.png", png_bytes)

    return str(zip_path)


# ---------------------------------------------------------------------------
# User manager fixture (isolated per-test)
# ---------------------------------------------------------------------------

@pytest.fixture()
def user_manager(tmp_path, monkeypatch):
    """UserManager backed by a temp directory so tests don't touch real data."""
    app_dir = tmp_path / ".zip-browser"
    app_dir.mkdir()

    class _TestUserManager(UserManager):
        def _get_users_file_path(self):
            return app_dir / "users.json"

    return _TestUserManager()


@pytest.fixture()
def auth_manager(user_manager):
    """AuthManager wired to the test UserManager."""
    return AuthManager(user_manager)


# ---------------------------------------------------------------------------
# Flask app fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(sample_zip, user_manager, auth_manager):
    """Create a Flask test application."""
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

    config = Config(zip_paths=[sample_zip], debug=True, multiuser=True)

    flask_app = Flask(__name__, template_folder="../src/templates",
                      static_folder="../src/static")
    flask_app.secret_key = config.secret_key
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False  # disable CSRF for test POSTs

    login_manager = LoginManager()
    login_manager.init_app(flask_app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return auth_manager.load_user(user_id)

    @flask_app.context_processor
    def utility_processor():
        return dict(get_file_icon=get_file_icon)

    flask_app.jinja_env.globals["csrf_token"] = generate_csrf

    zm = ZipManager()
    zm.initialize_zip_files([sample_zip])

    flask_app.register_blueprint(create_auth_routes(auth_manager))
    flask_app.register_blueprint(create_browse_routes(zm))
    flask_app.register_blueprint(create_video_routes(zm))
    flask_app.register_blueprint(create_search_routes(zm))

    flask_app.config["APP_CONFIG"] = config
    flask_app.config["_ZIP_MANAGER"] = zm

    return flask_app


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture()
def logged_in_client(app, client, user_manager):
    """Flask test client already authenticated."""
    user_manager.create_user("testuser", "testpass123", is_admin=False)
    with client.session_transaction() as sess:
        pass  # ensure session exists
    # Login via POST
    client.post("/login", data={"username": "testuser", "password": "testpass123"})
    return client
