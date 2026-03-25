"""
Unit tests for src/utils.py
"""
import os
import tempfile

from src.utils import (
    get_zip_file_hash,
    is_image,
    is_video,
    is_browser_native_video,
    needs_transcoding,
    is_system_file,
    should_show_file,
    get_file_icon,
    validate_pagination_params,
)


# ---------------------------------------------------------------------------
# get_zip_file_hash
# ---------------------------------------------------------------------------

class TestGetZipFileHash:
    def test_local_file_hash(self, tmp_path):
        f = tmp_path / "test.zip"
        f.write_text("data")
        h = get_zip_file_hash(str(f))
        assert isinstance(h, str)
        assert len(h) == 12

    def test_same_file_same_hash(self, tmp_path):
        f = tmp_path / "test.zip"
        f.write_text("data")
        assert get_zip_file_hash(str(f)) == get_zip_file_hash(str(f))

    def test_url_hash(self):
        url = "https://example.com/archive.zip"
        h = get_zip_file_hash(url)
        assert isinstance(h, str)
        assert len(h) == 12

    def test_url_deterministic(self):
        url = "https://example.com/archive.zip"
        assert get_zip_file_hash(url) == get_zip_file_hash(url)


# ---------------------------------------------------------------------------
# is_image
# ---------------------------------------------------------------------------

class TestIsImage:
    def test_known_extensions(self):
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
            assert is_image(f"file{ext}") is True

    def test_case_insensitive(self):
        assert is_image("PHOTO.JPG") is True
        assert is_image("image.Png") is True

    def test_non_image(self):
        assert is_image("file.txt") is False
        assert is_image("video.mp4") is False

    def test_no_extension(self):
        assert is_image("README") is False


# ---------------------------------------------------------------------------
# is_video / is_browser_native_video / needs_transcoding
# ---------------------------------------------------------------------------

class TestVideoUtils:
    def test_known_video_extensions(self):
        for ext in (".mp4", ".webm", ".ogg", ".mov", ".avi", ".mkv", ".m4v",
                     ".wmv", ".flv", ".3gp"):
            assert is_video(f"file{ext}") is True

    def test_non_video(self):
        assert is_video("file.txt") is False
        assert is_video("photo.jpg") is False

    def test_browser_native(self):
        assert is_browser_native_video("clip.mp4") is True
        assert is_browser_native_video("clip.webm") is True
        assert is_browser_native_video("clip.ogg") is True
        assert is_browser_native_video("clip.mkv") is False

    def test_needs_transcoding(self):
        assert needs_transcoding("movie.mkv") is True
        assert needs_transcoding("movie.avi") is True
        assert needs_transcoding("movie.mp4") is False
        assert needs_transcoding("photo.jpg") is False


# ---------------------------------------------------------------------------
# is_system_file / should_show_file
# ---------------------------------------------------------------------------

class TestSystemFileFiltering:
    def test_macos_metadata(self):
        assert is_system_file("._resource") is True
        assert is_system_file("__MACOSX/file") is True
        assert is_system_file(".DS_Store") is True

    def test_windows_metadata(self):
        assert is_system_file("Thumbs.db") is True
        assert is_system_file("desktop.ini") is True

    def test_normal_files(self):
        assert is_system_file("readme.txt") is False
        assert is_system_file("photo.jpg") is False

    def test_should_show_file(self):
        assert should_show_file("readme.txt") is True
        assert should_show_file("._hidden") is False
        assert should_show_file("Thumbs.db") is False


# ---------------------------------------------------------------------------
# get_file_icon
# ---------------------------------------------------------------------------

class TestGetFileIcon:
    def test_known_icons(self):
        assert get_file_icon(".pdf") == "icon-file-pdf"
        assert get_file_icon(".py") == "icon-file-code"
        assert get_file_icon(".mp4") == "icon-file-video"
        assert get_file_icon(".mp3") == "icon-file-audio"

    def test_case_insensitive(self):
        assert get_file_icon(".PDF") == "icon-file-pdf"

    def test_unknown_extension(self):
        assert get_file_icon(".xyz") == "icon-file"


# ---------------------------------------------------------------------------
# validate_pagination_params
# ---------------------------------------------------------------------------

class TestValidatePaginationParams:
    def test_valid_values(self):
        assert validate_pagination_params(2, 50, 150) == (2, 50, 150)

    def test_invalid_page(self):
        page, _, _ = validate_pagination_params(-1, 30, 100)
        assert page == 1
        page, _, _ = validate_pagination_params("abc", 30, 100)
        assert page == 1

    def test_invalid_per_page(self):
        _, per_page, _ = validate_pagination_params(1, 200, 100)
        assert per_page == 100  # clamped to max
        _, per_page, _ = validate_pagination_params(1, "bad", 100)
        assert per_page == 30

    def test_invalid_thumb_size(self):
        _, _, ts = validate_pagination_params(1, 30, 999)
        assert ts == 100  # not in allowed list
        _, _, ts = validate_pagination_params(1, 30, "bad")
        assert ts == 100

    def test_none_values(self):
        assert validate_pagination_params(None, None, None) == (1, 30, 100)
