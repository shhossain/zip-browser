"""
Tests for the "Open With" feature — user preferences and open-with routes.
"""
import json
import os

import pytest

from src.user_manager import UserManager
from src.zip_manager import ZipManager
from src.utils import get_source_hash


# ==============================================================
# UserManager preferences
# ==============================================================
class TestUserManagerPreferences:
    def test_get_preferences_empty(self, user_manager):
        user_manager.create_user("alice", "pass123")
        prefs = user_manager.get_preferences("alice")
        assert prefs == {}

    def test_set_and_get_preference(self, user_manager):
        user_manager.create_user("alice", "pass123")
        user_manager.set_preference("alice", "theme", "dark")
        prefs = user_manager.get_preferences("alice")
        assert prefs["theme"] == "dark"

    def test_set_preference_nonexistent_user(self, user_manager):
        assert user_manager.set_preference("ghost", "theme", "dark") is False

    def test_get_open_with_prefs_empty(self, user_manager):
        user_manager.create_user("alice", "pass123")
        assert user_manager.get_open_with_prefs("alice") == {}

    def test_set_open_with_pref(self, user_manager):
        user_manager.create_user("alice", "pass123")
        ok = user_manager.set_open_with_pref("alice", ".bin", "text")
        assert ok is True
        ow = user_manager.get_open_with_prefs("alice")
        assert ow[".bin"] == "text"

    def test_set_open_with_pref_normalizes_extension(self, user_manager):
        user_manager.create_user("alice", "pass123")
        user_manager.set_open_with_pref("alice", ".BIN", "text")
        ow = user_manager.get_open_with_prefs("alice")
        assert ".bin" in ow

    def test_set_multiple_open_with_prefs(self, user_manager):
        user_manager.create_user("alice", "pass123")
        user_manager.set_open_with_pref("alice", ".bin", "text")
        user_manager.set_open_with_pref("alice", ".dat", "download")
        ow = user_manager.get_open_with_prefs("alice")
        assert ow[".bin"] == "text"
        assert ow[".dat"] == "download"

    def test_overwrite_open_with_pref(self, user_manager):
        user_manager.create_user("alice", "pass123")
        user_manager.set_open_with_pref("alice", ".bin", "text")
        user_manager.set_open_with_pref("alice", ".bin", "image")
        ow = user_manager.get_open_with_prefs("alice")
        assert ow[".bin"] == "image"

    def test_open_with_pref_nonexistent_user(self, user_manager):
        assert user_manager.set_open_with_pref("ghost", ".bin", "text") is False

    def test_preferences_persist_across_loads(self, user_manager):
        user_manager.create_user("alice", "pass123")
        user_manager.set_open_with_pref("alice", ".bin", "text")
        # Re-read directly from disk
        ow = user_manager.get_open_with_prefs("alice")
        assert ow[".bin"] == "text"


# ==============================================================
# Open-with routes
# ==============================================================
class TestOpenWithRoutes:
    def test_open_with_text(self, logged_in_client, sample_zip):
        zip_id = get_source_hash(sample_zip)
        # Load the archive
        logged_in_client.get(f"/browse/{zip_id}/")
        resp = logged_in_client.get(f"/open-with/text/{zip_id}/readme.txt")
        assert resp.status_code == 200
        assert resp.content_type == "text/plain; charset=utf-8"
        assert b"Hello World" in resp.data

    def test_open_with_download(self, logged_in_client, sample_zip):
        zip_id = get_source_hash(sample_zip)
        logged_in_client.get(f"/browse/{zip_id}/")
        resp = logged_in_client.get(f"/open-with/download/{zip_id}/readme.txt")
        assert resp.status_code == 200
        assert resp.content_type == "application/octet-stream"

    def test_open_with_image(self, logged_in_client, sample_zip):
        zip_id = get_source_hash(sample_zip)
        logged_in_client.get(f"/browse/{zip_id}/")
        resp = logged_in_client.get(f"/open-with/image/{zip_id}/readme.txt")
        assert resp.status_code == 200
        assert resp.content_type == "image/png"

    def test_open_with_default(self, logged_in_client, sample_zip):
        zip_id = get_source_hash(sample_zip)
        logged_in_client.get(f"/browse/{zip_id}/")
        resp = logged_in_client.get(f"/open-with/default/{zip_id}/readme.txt")
        assert resp.status_code == 200
        # text/plain is the guessed MIME for .txt
        assert "text/plain" in resp.content_type

    def test_open_with_invalid_handler(self, logged_in_client, sample_zip):
        zip_id = get_source_hash(sample_zip)
        logged_in_client.get(f"/browse/{zip_id}/")
        resp = logged_in_client.get(f"/open-with/foobar/{zip_id}/readme.txt")
        assert resp.status_code == 400

    def test_open_with_archive_redirects(self, logged_in_client, sample_zip):
        zip_id = get_source_hash(sample_zip)
        logged_in_client.get(f"/browse/{zip_id}/")
        resp = logged_in_client.get(f"/open-with/archive/{zip_id}/readme.txt")
        assert resp.status_code == 302

    def test_open_with_nonexistent_zip(self, logged_in_client):
        resp = logged_in_client.get("/open-with/text/fakeid123/readme.txt")
        assert resp.status_code == 404

    def test_open_with_options_returns_handlers(self, logged_in_client, sample_zip):
        zip_id = get_source_hash(sample_zip)
        logged_in_client.get(f"/browse/{zip_id}/")
        resp = logged_in_client.get(f"/open-with-options/{zip_id}/readme.txt")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "handlers" in data
        handler_ids = [h["id"] for h in data["handlers"]]
        assert "text" in handler_ids
        assert "download" in handler_ids
        assert "default" in handler_ids
        assert data["extension"] == ".txt"
        assert data["saved"] is None  # no preference saved yet

    def test_open_with_options_shows_saved_pref(self, logged_in_client, sample_zip, user_manager):
        zip_id = get_source_hash(sample_zip)
        logged_in_client.get(f"/browse/{zip_id}/")
        # Save a preference
        user_manager.set_open_with_pref("testuser", ".txt", "text")
        resp = logged_in_client.get(f"/open-with-options/{zip_id}/readme.txt")
        data = resp.get_json()
        assert data["saved"] == "text"


# ==============================================================
# Save open-with preference via route
# ==============================================================
class TestSaveOpenWithRoute:
    def test_save_open_with(self, logged_in_client, user_manager):
        resp = logged_in_client.post(
            "/save-open-with",
            data=json.dumps({"extension": ".bin", "handler": "text"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # Verify in storage
        ow = user_manager.get_open_with_prefs("testuser")
        assert ow[".bin"] == "text"

    def test_save_open_with_invalid_handler(self, logged_in_client):
        resp = logged_in_client.post(
            "/save-open-with",
            data=json.dumps({"extension": ".bin", "handler": "foobar"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_save_open_with_missing_extension(self, logged_in_client):
        resp = logged_in_client.post(
            "/save-open-with",
            data=json.dumps({"extension": "", "handler": "text"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_save_open_with_no_json_body(self, logged_in_client):
        resp = logged_in_client.post("/save-open-with")
        assert resp.status_code == 400
