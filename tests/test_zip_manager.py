"""
Unit tests for src/zip_manager.py
"""
import os
import zipfile

import pytest

from src.zip_manager import ZipManager
from src.utils import get_zip_file_hash


class TestZipManagerUrlDetection:
    def test_http_url(self):
        zm = ZipManager()
        assert zm.is_url("http://example.com/file.zip") is True

    def test_https_url(self):
        zm = ZipManager()
        assert zm.is_url("https://example.com/file.zip") is True

    def test_ftp_url(self):
        zm = ZipManager()
        assert zm.is_url("ftp://example.com/file.zip") is False

    def test_local_path(self):
        zm = ZipManager()
        assert zm.is_url("/tmp/archive.zip") is False
        assert zm.is_url("archive.zip") is False


class TestZipManagerDiscover:
    def test_discover_single_file(self, sample_zip):
        zm = ZipManager()
        files = zm.discover_zip_files(sample_zip)
        assert files == [sample_zip]

    def test_discover_directory(self, tmp_path, sample_zip):
        import shutil
        dest = tmp_path / "zips"
        dest.mkdir()
        shutil.copy(sample_zip, dest / "a.zip")
        shutil.copy(sample_zip, dest / "b.zip")
        zm = ZipManager()
        found = zm.discover_zip_files(str(dest))
        assert len(found) >= 2

    def test_discover_nonexistent(self):
        zm = ZipManager()
        assert zm.discover_zip_files("/nonexistent/path") == []

    def test_discover_url_file(self, tmp_path):
        txt = tmp_path / "urls.txt"
        txt.write_text("# comment\nhttps://example.com/a.zip\nhttps://example.com/b.zip\n")
        zm = ZipManager()
        urls = zm.discover_zip_files(str(txt))
        assert len(urls) == 2
        assert all(u.startswith("https://") for u in urls)


class TestZipManagerTree:
    def test_build_tree(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        tree = zm.zip_files[zip_id]["tree"]
        # Top-level entries should include readme.txt, docs/, images/, videos/
        assert "readme.txt" in tree
        assert "docs" in tree
        assert "images" in tree
        # System files should be filtered
        assert ".__hidden" not in tree
        assert "Thumbs.db" not in tree

    def test_get_dir_tree_root(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        root = zm.get_dir_tree(zip_id, "")
        assert root is not None
        assert "readme.txt" in root

    def test_get_dir_tree_subfolder(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        sub = zm.get_dir_tree(zip_id, "images")
        assert sub is not None
        assert "photo.jpg" in sub

    def test_get_dir_tree_invalid_path(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        assert zm.get_dir_tree(zip_id, "nonexistent") is None


class TestZipManagerPassword:
    def test_no_password_required(self, sample_zip):
        zm = ZipManager()
        assert zm.check_zip_requires_password(sample_zip) is False

    def test_password_required(self, password_zip):
        zm = ZipManager()
        assert zm.check_zip_requires_password(password_zip) is True

    def test_validate_correct_password(self, password_zip):
        zm = ZipManager()
        assert zm.validate_zip_password(password_zip, "secret123") is True

    def test_validate_wrong_password(self, password_zip):
        zm = ZipManager()
        assert zm.validate_zip_password(password_zip, "wrong") is False


class TestZipManagerSearch:
    def test_search_all(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        results = zm.search_files(zip_id, "photo")
        assert len(results) >= 1
        assert results[0]["name"] == "photo.jpg"

    def test_search_images_only(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        results = zm.search_files(zip_id, "photo", "images")
        assert all(r["is_image"] for r in results)

    def test_search_no_match(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        results = zm.search_files(zip_id, "xyznonexistent")
        assert results == []

    def test_search_empty_query(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        results = zm.search_files(zip_id, "")
        assert results == []


class TestZipManagerMisc:
    def test_get_zip_info(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        info = zm.get_zip_info(zip_id)
        assert info is not None
        assert info["name"] == "sample.zip"
        assert info["is_remote"] is False

    def test_get_all_zip_files(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        all_zips = zm.get_all_zip_files()
        assert len(all_zips) == 1

    def test_get_first_image_in_folder(self, sample_zip):
        zm = ZipManager()
        zm.initialize_zip_files([sample_zip])
        zip_id = get_zip_file_hash(sample_zip)
        zm.load_zip_file(zip_id)

        first = zm.get_first_image_in_folder(zip_id, "images")
        assert first is not None
        assert first.endswith((".jpg", ".png"))
