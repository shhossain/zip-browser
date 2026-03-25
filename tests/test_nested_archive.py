"""
Tests for nested archive support — archives inside archives.
"""
import os
import zipfile

import pytest

from src.archive_handler import is_nested_archive, is_supported_archive
from src.zip_manager import ZipManager
from src.utils import get_zip_file_hash


# ==============================================================
# is_nested_archive helper
# ==============================================================
class TestIsNestedArchive:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("inner.zip", True),
            ("inner.tar", True),
            ("inner.tar.gz", True),
            ("inner.tar.bz2", True),
            ("inner.tar.xz", True),
            ("inner.7z", True),
            ("inner.rar", True),
            ("inner.iso", True),
            # Standalone .gz excluded — single compressed file, not browsable
            ("data.gz", False),
            # Non-archive files
            ("readme.txt", False),
            ("photo.jpg", False),
            ("video.mp4", False),
        ],
    )
    def test_is_nested_archive(self, filename, expected):
        assert is_nested_archive(filename) == expected


# ==============================================================
# Tree building with __archive__ sentinel
# ==============================================================
class TestTreeBuildingWithNestedArchives:
    def test_nested_zip_marked_in_tree(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        tree = zm.zip_files[zip_id]["tree"]
        assert "top_level.txt" in tree
        assert tree["top_level.txt"] is None  # regular file
        assert "archives" in tree
        assert tree["archives"]["inner.zip"] == "__archive__"

    def test_nested_tar_marked_in_tree(self, nested_tar_in_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_tar_in_zip])
        zip_id = get_zip_file_hash(nested_tar_in_zip)
        zm.load_zip_file(zip_id)

        tree = zm.zip_files[zip_id]["tree"]
        assert tree["inner.tar"] == "__archive__"

    def test_is_item_archive(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        assert zm.is_item_archive(zip_id, "archives/inner.zip")
        assert not zm.is_item_archive(zip_id, "top_level.txt")
        assert not zm.is_item_archive(zip_id, "archives")
        assert not zm.is_item_archive(zip_id, "nonexistent.zip")


# ==============================================================
# Opening nested archives
# ==============================================================
class TestOpenNestedArchive:
    def test_open_nested_zip(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        nested_id, needs_password = zm.open_nested_archive(zip_id, "archives/inner.zip")
        assert nested_id is not None
        assert needs_password is False

        # The nested archive should now be browsable
        nested_info = zm.get_zip_info(nested_id)
        assert nested_info is not None
        assert nested_info["nested"] is True
        assert nested_info["name"] == "inner.zip"

        # Its tree should contain the inner files
        tree = nested_info["tree"]
        assert "inner_readme.txt" in tree
        assert "inner_dir" in tree
        assert "file.txt" in tree["inner_dir"]

    def test_open_nested_tar(self, nested_tar_in_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_tar_in_zip])
        zip_id = get_zip_file_hash(nested_tar_in_zip)
        zm.load_zip_file(zip_id)

        nested_id, needs_password = zm.open_nested_archive(zip_id, "inner.tar")
        assert nested_id is not None
        assert needs_password is False

        tree = zm.get_zip_info(nested_id)["tree"]
        assert "tar_file.txt" in tree

    def test_read_file_from_nested_archive(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        nested_id, _ = zm.open_nested_archive(zip_id, "archives/inner.zip")
        zfile = zm.get_zip_file_object(nested_id)
        assert zfile is not None
        data = zfile.read("inner_readme.txt")
        assert data == b"Hello from inner archive"
        zfile.close()

    def test_nested_archive_id_is_deterministic(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        id1 = zm.get_nested_archive_id(zip_id, "archives/inner.zip")
        id2 = zm.get_nested_archive_id(zip_id, "archives/inner.zip")
        assert id1 == id2

    def test_reopen_returns_existing_nested(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        id1, _ = zm.open_nested_archive(zip_id, "archives/inner.zip")
        id2, _ = zm.open_nested_archive(zip_id, "archives/inner.zip")
        assert id1 == id2

    def test_open_nonexistent_inner_path(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        nested_id, needs_pw = zm.open_nested_archive(zip_id, "nope.zip")
        assert nested_id is None

    def test_open_from_nonexistent_parent(self):
        zm = ZipManager()
        nested_id, needs_pw = zm.open_nested_archive("fake_id", "inner.zip")
        assert nested_id is None


# ==============================================================
# Password-protected nested archive
# ==============================================================
class TestNestedPasswordArchive:
    def test_nested_password_detected(self, nested_password_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_password_zip])
        zip_id = get_zip_file_hash(nested_password_zip)
        zm.load_zip_file(zip_id)

        nested_id, needs_password = zm.open_nested_archive(zip_id, "secret_inner.zip")
        assert nested_id is not None
        assert needs_password is True

        # The nested entry should exist but not be loaded
        nested_info = zm.get_zip_info(nested_id)
        assert nested_info["requires_password"] is True
        assert nested_info["zfile"] is None

    def test_nested_password_unlock(self, nested_password_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_password_zip])
        zip_id = get_zip_file_hash(nested_password_zip)
        zm.load_zip_file(zip_id)

        # First open — should request password
        nested_id, needs_password = zm.open_nested_archive(zip_id, "secret_inner.zip")
        assert needs_password is True

        # Now provide the correct password
        nested_id2, needs_password2 = zm.open_nested_archive(
            zip_id, "secret_inner.zip", password="inner_pass"
        )
        assert nested_id2 == nested_id
        assert needs_password2 is False

        # Should be able to read contents
        zfile = zm.get_zip_file_object(nested_id)
        data = zfile.read("secret.txt")
        assert data == b"Inner secret"
        zfile.close()

    def test_nested_wrong_password(self, nested_password_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_password_zip])
        zip_id = get_zip_file_hash(nested_password_zip)
        zm.load_zip_file(zip_id)

        nested_id, _ = zm.open_nested_archive(zip_id, "secret_inner.zip")
        # Try wrong password — load_zip_file returns None
        result = zm.load_zip_file(nested_id, password="wrong")
        assert result is None


# ==============================================================
# Cleanup
# ==============================================================
class TestNestedArchiveCleanup:
    def test_cleanup_removes_temp_files(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        nested_id, _ = zm.open_nested_archive(zip_id, "archives/inner.zip")
        tmp_dir = zm.zip_files[nested_id]["_tmp_dir"]
        assert os.path.isdir(tmp_dir)

        zm.cleanup_nested_archives()
        assert not os.path.isdir(tmp_dir)
        assert nested_id not in zm.zip_files


# ==============================================================
# get_dir_tree with nested archives
# ==============================================================
class TestGetDirTreeWithNestedArchives:
    def test_get_dir_tree_returns_archive_sentinel(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        # Navigating to the directory containing the archive
        tree = zm.get_dir_tree(zip_id, "archives")
        assert tree == {"inner.zip": "__archive__"}

    def test_get_dir_tree_path_to_archive_returns_sentinel(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        result = zm.get_dir_tree(zip_id, "archives/inner.zip")
        assert result == "__archive__"


# ==============================================================
# Search includes nested archive entries
# ==============================================================
class TestSearchWithNestedArchives:
    def test_search_finds_nested_archive(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        results = zm.search_files(zip_id, "inner")
        names = [r["name"] for r in results]
        assert "inner.zip" in names

        inner_result = next(r for r in results if r["name"] == "inner.zip")
        assert inner_result["is_archive"] is True
        assert inner_result["is_folder"] is False

    def test_search_type_files_includes_archives(self, nested_zip):
        zm = ZipManager()
        zm.initialize_zip_files([nested_zip])
        zip_id = get_zip_file_hash(nested_zip)
        zm.load_zip_file(zip_id)

        results = zm.search_files(zip_id, "inner", search_type="files")
        names = [r["name"] for r in results]
        assert "inner.zip" in names


# ==============================================================
# Route integration — browse into nested archive
# ==============================================================
class TestNestedArchiveRoutes:
    @pytest.fixture()
    def nested_app(self, nested_zip, user_manager, auth_manager):
        """Flask test app with a nested-zip archive loaded."""
        from flask import Flask
        from flask_login import LoginManager
        from flask_wtf.csrf import generate_csrf
        from src.routes import (
            create_auth_routes,
            create_browse_routes,
            create_video_routes,
            create_search_routes,
        )
        from src.utils import get_file_icon
        from src.config import Config

        config = Config(zip_paths=[nested_zip], debug=True, multiuser=True)
        flask_app = Flask(
            __name__,
            template_folder="../src/templates",
            static_folder="../src/static",
        )
        flask_app.secret_key = config.secret_key
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

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
        zm.initialize_zip_files([nested_zip])

        flask_app.register_blueprint(create_auth_routes(auth_manager))
        flask_app.register_blueprint(create_browse_routes(zm))
        flask_app.register_blueprint(create_video_routes(zm))
        flask_app.register_blueprint(create_search_routes(zm))

        flask_app.config["APP_CONFIG"] = config
        flask_app.config["_ZIP_MANAGER"] = zm

        return flask_app

    @pytest.fixture()
    def nested_client(self, nested_app, user_manager):
        client = nested_app.test_client()
        user_manager.create_user("testuser", "testpass123", is_admin=False)
        client.post("/login", data={"username": "testuser", "password": "testpass123"})
        return client

    def test_browse_shows_archive_item(self, nested_client, nested_zip):
        zip_id = get_zip_file_hash(nested_zip)
        # Load the outer archive first
        resp = nested_client.get(f"/browse/{zip_id}/")
        assert resp.status_code == 200

        # Browse into the archives directory
        resp = nested_client.get(f"/browse/{zip_id}/archives")
        assert resp.status_code == 200
        assert b"inner.zip" in resp.data

    def test_browse_into_nested_archive_redirects(self, nested_client, nested_zip):
        zip_id = get_zip_file_hash(nested_zip)
        # Load outer
        nested_client.get(f"/browse/{zip_id}/")
        # Navigate to the nested archive — should redirect
        resp = nested_client.get(f"/browse/{zip_id}/archives/inner.zip")
        assert resp.status_code == 302  # redirect to nested archive browse

    def test_browse_nested_archive_contents(self, nested_client, nested_zip):
        zip_id = get_zip_file_hash(nested_zip)
        # Load outer
        nested_client.get(f"/browse/{zip_id}/")
        # Follow redirect into nested archive
        resp = nested_client.get(
            f"/browse/{zip_id}/archives/inner.zip", follow_redirects=True
        )
        assert resp.status_code == 200
        assert b"inner_readme.txt" in resp.data

    def test_browse_nested_password_shows_unlock(
        self, nested_password_zip, user_manager, auth_manager
    ):
        """Browsing into a password-protected nested archive shows unlock form."""
        from flask import Flask
        from flask_login import LoginManager
        from flask_wtf.csrf import generate_csrf
        from src.routes import (
            create_auth_routes,
            create_browse_routes,
            create_video_routes,
            create_search_routes,
        )
        from src.utils import get_file_icon
        from src.config import Config

        config = Config(zip_paths=[nested_password_zip], debug=True, multiuser=True)
        flask_app = Flask(
            __name__,
            template_folder="../src/templates",
            static_folder="../src/static",
        )
        flask_app.secret_key = config.secret_key
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

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
        zm.initialize_zip_files([nested_password_zip])

        flask_app.register_blueprint(create_auth_routes(auth_manager))
        flask_app.register_blueprint(create_browse_routes(zm))
        flask_app.register_blueprint(create_video_routes(zm))
        flask_app.register_blueprint(create_search_routes(zm))

        flask_app.config["_ZIP_MANAGER"] = zm

        client = flask_app.test_client()
        user_manager.create_user("testuser", "testpass123", is_admin=False)
        client.post("/login", data={"username": "testuser", "password": "testpass123"})

        zip_id = get_zip_file_hash(nested_password_zip)
        # Load outer
        client.get(f"/browse/{zip_id}/")
        # Browse to password-protected nested archive
        resp = client.get(f"/browse/{zip_id}/secret_inner.zip")
        assert resp.status_code == 200
        assert b"Password Required" in resp.data
