"""
Integration tests for Flask routes (auth, browse, search).
Uses Flask test client with CSRF disabled.
"""
import io
import zipfile

import pytest

from src.utils import get_zip_file_hash


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

class TestAuthRoutes:
    def test_index_redirects_to_login(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 308)
        assert "/login" in resp.headers["Location"]

    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Login" in resp.data or b"login" in resp.data

    def test_login_success(self, client, user_manager):
        user_manager.create_user("alice", "pass123")
        resp = client.post("/login", data={"username": "alice", "password": "pass123"},
                           follow_redirects=True)
        assert resp.status_code == 200
        # Should land on zip list page after login
        assert b"alice" in resp.data or b"zip" in resp.data.lower()

    def test_login_failure(self, client, user_manager):
        user_manager.create_user("alice", "pass123")
        resp = client.post("/login", data={"username": "alice", "password": "wrong"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid" in resp.data or b"invalid" in resp.data

    def test_logout(self, logged_in_client):
        resp = logged_in_client.get("/logout", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Login" in resp.data or b"login" in resp.data

    def test_protected_route_requires_login(self, client):
        resp = client.get("/zips", follow_redirects=False)
        assert resp.status_code in (302, 401)


# ---------------------------------------------------------------------------
# Browse routes
# ---------------------------------------------------------------------------

class TestBrowseRoutes:
    def test_zip_list(self, logged_in_client):
        resp = logged_in_client.get("/zips")
        assert resp.status_code == 200
        assert b"sample.zip" in resp.data

    def test_browse_root(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/browse/{zip_id}/")
        assert resp.status_code == 200
        assert b"readme.txt" in resp.data

    def test_browse_subfolder(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/browse/{zip_id}/docs")
        assert resp.status_code == 200
        assert b"guide.md" in resp.data

    def test_browse_invalid_zip(self, logged_in_client):
        resp = logged_in_client.get("/browse/nonexistent/")
        assert resp.status_code == 404

    def test_browse_invalid_path(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/browse/{zip_id}/no_such_folder")
        assert resp.status_code == 404

    def test_browse_pagination_params(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(
            f"/browse/{zip_id}/?view=details&sort=name&order=desc&page=1&per_page=10&thumb_size=150"
        )
        assert resp.status_code == 200

    def test_view_file(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/view/{zip_id}/readme.txt")
        assert resp.status_code == 200
        assert b"Hello World" in resp.data

    def test_view_file_not_found(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/view/{zip_id}/nonexistent.txt")
        # The route catches KeyError and returns 200 with an error message
        assert resp.status_code == 200
        assert b"Error" in resp.data


# ---------------------------------------------------------------------------
# Thumbnail route
# ---------------------------------------------------------------------------

class TestThumbnailRoute:
    def test_thumbnail_generation(self, app, user_manager, image_zip):
        """Test thumbnail endpoint with a real image ZIP."""
        from flask import Flask
        from flask_login import LoginManager
        from flask_wtf.csrf import generate_csrf
        from src.routes import create_auth_routes, create_browse_routes, create_video_routes, create_search_routes
        from src.auth import AuthManager
        from src.config import Config
        from src.utils import get_file_icon

        config = Config(zip_paths=[image_zip], debug=True, multiuser=True)
        flask_app = Flask(__name__, template_folder="../src/templates",
                          static_folder="../src/static")
        flask_app.secret_key = config.secret_key
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

        am = AuthManager(user_manager)

        login_manager = LoginManager()
        login_manager.init_app(flask_app)
        login_manager.login_view = "auth.login"

        @login_manager.user_loader
        def load_user(uid):
            return am.load_user(uid)

        @flask_app.context_processor
        def ctx():
            return dict(get_file_icon=get_file_icon)

        flask_app.jinja_env.globals["csrf_token"] = generate_csrf

        from src.zip_manager import ZipManager
        zm = ZipManager()
        zm.initialize_zip_files([image_zip])

        flask_app.register_blueprint(create_auth_routes(am))
        flask_app.register_blueprint(create_browse_routes(zm))
        flask_app.register_blueprint(create_video_routes(zm))
        flask_app.register_blueprint(create_search_routes(zm))

        user_manager.create_user("imguser", "pass")

        with flask_app.test_client() as c:
            c.post("/login", data={"username": "imguser", "password": "pass"})
            zip_id = get_zip_file_hash(image_zip)
            resp = c.get(f"/thumb/{zip_id}/photo.jpg?size=100")
            assert resp.status_code == 200
            assert resp.content_type == "image/jpeg"

    def test_thumbnail_invalid_zip(self, logged_in_client):
        resp = logged_in_client.get("/thumb/nonexistent/photo.jpg")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Search routes
# ---------------------------------------------------------------------------

class TestSearchRoutes:
    def test_search_page(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/search/{zip_id}?q=readme")
        assert resp.status_code == 200

    def test_search_empty_query(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/search/{zip_id}?q=")
        assert resp.status_code == 200

    def test_search_no_results(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        resp = logged_in_client.get(f"/search/{zip_id}?q=xyznonexistent")
        assert resp.status_code == 200

    def test_search_invalid_zip(self, logged_in_client):
        resp = logged_in_client.get("/search/nonexistent?q=test")
        assert resp.status_code == 404

    def test_search_with_type_filter(self, logged_in_client, sample_zip):
        zip_id = get_zip_file_hash(sample_zip)
        for stype in ("all", "images", "videos", "folders", "files"):
            resp = logged_in_client.get(f"/search/{zip_id}?q=a&type={stype}")
            assert resp.status_code == 200
