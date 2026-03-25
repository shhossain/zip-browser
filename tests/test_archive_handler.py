"""
Tests for src/archive_handler.py — unified archive interface.
"""
import gzip
import io
import os
import tarfile
import zipfile

import pytest

from src.archive_handler import (
    ArchiveFile,
    open_archive,
    get_archive_ext,
    is_supported_archive,
    ARCHIVE_GLOB_PATTERNS,
)

# Optional archive libraries
try:
    import py7zr

    HAS_7Z = True
except ImportError:
    HAS_7Z = False

try:
    import rarfile as _rarfile

    HAS_RAR = True
except ImportError:
    HAS_RAR = False


# ------------------------------------------------------------------ helpers
def _make_zip(tmp_path, name="test.zip", files=None):
    files = files or {"hello.txt": b"Hello World"}
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        for fname, data in files.items():
            zf.writestr(fname, data)
    return str(p)


def _make_tar(tmp_path, name="test.tar", mode="w", files=None):
    files = files or {"hello.txt": b"Hello World"}
    p = tmp_path / name
    with tarfile.open(p, mode) as tf:
        for fname, data in files.items():
            info = tarfile.TarInfo(name=fname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(p)


def _make_gz(tmp_path, name="readme.txt.gz", content=b"gz content"):
    p = tmp_path / name
    with gzip.open(p, "wb") as f:
        f.write(content)
    return str(p)


# ==============================================================
# get_archive_ext / is_supported_archive
# ==============================================================
class TestArchiveExtDetection:
    @pytest.mark.parametrize(
        "path, expected",
        [
            ("archive.zip", ".zip"),
            ("archive.ZIP", ".zip"),
            ("archive.tar", ".tar"),
            ("archive.tar.gz", ".tar.gz"),
            ("archive.tgz", ".tgz"),
            ("archive.tar.bz2", ".tar.bz2"),
            ("archive.tbz2", ".tbz2"),
            ("archive.tar.xz", ".tar.xz"),
            ("archive.txz", ".txz"),
            ("archive.7z", ".7z"),
            ("archive.rar", ".rar"),
            ("archive.gz", ".gz"),
            ("archive.iso", ".iso"),
        ],
    )
    def test_get_archive_ext(self, path, expected):
        assert get_archive_ext(path) == expected

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("archive.zip", True),
            ("archive.tar.gz", True),
            ("archive.7z", True),
            ("archive.rar", True),
            ("document.pdf", False),
            ("image.jpg", False),
        ],
    )
    def test_is_supported_archive(self, path, expected):
        assert is_supported_archive(path) == expected


# ==============================================================
# ZIP archives
# ==============================================================
class TestZipArchive:
    def test_namelist(self, tmp_path):
        path = _make_zip(tmp_path, files={"a.txt": b"A", "dir/b.txt": b"B"})
        with open_archive(path) as af:
            names = af.namelist()
            assert "a.txt" in names
            assert "dir/b.txt" in names

    def test_read(self, tmp_path):
        path = _make_zip(tmp_path, files={"data.bin": b"\x01\x02\x03"})
        with open_archive(path) as af:
            assert af.read("data.bin") == b"\x01\x02\x03"

    def test_context_manager(self, tmp_path):
        path = _make_zip(tmp_path)
        af = open_archive(path)
        af.close()
        # Should not raise even if closed twice
        af.close()


# ==============================================================
# TAR archives (plain, gz, bz2, xz)
# ==============================================================
class TestTarArchive:
    def test_tar_plain(self, tmp_path):
        path = _make_tar(tmp_path, "test.tar", "w")
        with open_archive(path) as af:
            assert "hello.txt" in af.namelist()
            assert af.read("hello.txt") == b"Hello World"

    def test_tar_gz(self, tmp_path):
        path = _make_tar(tmp_path, "test.tar.gz", "w:gz")
        with open_archive(path) as af:
            assert "hello.txt" in af.namelist()
            assert af.read("hello.txt") == b"Hello World"

    def test_tar_bz2(self, tmp_path):
        path = _make_tar(tmp_path, "test.tar.bz2", "w:bz2")
        with open_archive(path) as af:
            assert "hello.txt" in af.namelist()
            assert af.read("hello.txt") == b"Hello World"

    def test_tar_xz(self, tmp_path):
        path = _make_tar(tmp_path, "test.tar.xz", "w:xz")
        with open_archive(path) as af:
            assert "hello.txt" in af.namelist()
            assert af.read("hello.txt") == b"Hello World"

    def test_tgz_extension(self, tmp_path):
        """Test .tgz alias for .tar.gz."""
        # Create as tar.gz then rename to .tgz
        gz_path = _make_tar(tmp_path, "test.tar.gz", "w:gz")
        tgz_path = str(tmp_path / "test.tgz")
        os.rename(gz_path, tgz_path)
        with open_archive(tgz_path) as af:
            assert "hello.txt" in af.namelist()

    def test_tar_with_directories(self, tmp_path):
        files = {"readme.txt": b"hi", "sub/file.txt": b"sub file"}
        path = _make_tar(tmp_path, files=files)
        with open_archive(path) as af:
            names = af.namelist()
            assert "readme.txt" in names
            assert "sub/file.txt" in names

    def test_tar_read_nonexistent_raises(self, tmp_path):
        path = _make_tar(tmp_path)
        with open_archive(path) as af:
            with pytest.raises(KeyError):
                af.read("nonexistent.txt")


# ==============================================================
# Standalone GZ files
# ==============================================================
class TestGzArchive:
    def test_gz_namelist(self, tmp_path):
        path = _make_gz(tmp_path, "data.txt.gz")
        with open_archive(path) as af:
            assert af.namelist() == ["data.txt"]

    def test_gz_read(self, tmp_path):
        path = _make_gz(tmp_path, content=b"compressed data")
        with open_archive(path) as af:
            name = af.namelist()[0]
            assert af.read(name) == b"compressed data"

    def test_gz_read_wrong_name_raises(self, tmp_path):
        path = _make_gz(tmp_path)
        with open_archive(path) as af:
            with pytest.raises(KeyError):
                af.read("wrong.txt")


# ==============================================================
# 7Z archives
# ==============================================================
@pytest.mark.skipif(not HAS_7Z, reason="py7zr not installed")
class TestSevenZipArchive:
    def test_namelist(self, sample_7z):
        with open_archive(sample_7z) as af:
            names = af.namelist()
            assert "readme.txt" in names

    def test_read(self, sample_7z):
        with open_archive(sample_7z) as af:
            data = af.read("readme.txt")
            assert data == b"Hello World"

    def test_read_nonexistent_raises(self, sample_7z):
        with open_archive(sample_7z) as af:
            with pytest.raises(KeyError):
                af.read("nonexistent.txt")


# ==============================================================
# ZipManager integration with non-zip archives
# ==============================================================
class TestZipManagerWithArchives:
    """Test that ZipManager properly discovers and loads non-zip archives."""

    def test_discover_tar_in_directory(self, mixed_archive_dir):
        from src.zip_manager import ZipManager

        zm = ZipManager()
        found = zm.discover_zip_files(mixed_archive_dir)
        extensions = {os.path.splitext(f)[1] for f in found}
        assert ".zip" in extensions
        assert ".tar" in extensions
        names = [os.path.basename(f) for f in found]
        assert "c.tar.gz" in names

    def test_load_tar_archive(self, sample_tar):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_tar])
        zip_id = get_zip_file_hash(sample_tar)
        result = zm.load_zip_file(zip_id)
        assert result is not None
        tree = zm.zip_files[zip_id]["tree"]
        assert "readme.txt" in tree

    def test_load_tar_gz_archive(self, sample_tar_gz):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_tar_gz])
        zip_id = get_zip_file_hash(sample_tar_gz)
        result = zm.load_zip_file(zip_id)
        assert result is not None
        tree = zm.zip_files[zip_id]["tree"]
        assert "readme.txt" in tree
        assert "docs" in tree

    def test_load_tar_bz2_archive(self, sample_tar_bz2):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_tar_bz2])
        zip_id = get_zip_file_hash(sample_tar_bz2)
        result = zm.load_zip_file(zip_id)
        assert result is not None

    def test_load_tar_xz_archive(self, sample_tar_xz):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_tar_xz])
        zip_id = get_zip_file_hash(sample_tar_xz)
        result = zm.load_zip_file(zip_id)
        assert result is not None

    def test_load_gz_archive(self, sample_gz):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_gz])
        zip_id = get_zip_file_hash(sample_gz)
        result = zm.load_zip_file(zip_id)
        assert result is not None
        tree = zm.zip_files[zip_id]["tree"]
        assert "readme.txt" in tree

    @pytest.mark.skipif(not HAS_7Z, reason="py7zr not installed")
    def test_load_7z_archive(self, sample_7z):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_7z])
        zip_id = get_zip_file_hash(sample_7z)
        result = zm.load_zip_file(zip_id)
        assert result is not None
        tree = zm.zip_files[zip_id]["tree"]
        assert "readme.txt" in tree

    def test_search_in_tar(self, sample_tar):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_tar])
        zip_id = get_zip_file_hash(sample_tar)
        zm.load_zip_file(zip_id)
        results = zm.search_files(zip_id, "readme")
        assert len(results) >= 1
        assert results[0]["name"] == "readme.txt"

    def test_get_file_object_tar(self, sample_tar):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_tar])
        zip_id = get_zip_file_hash(sample_tar)
        zm.load_zip_file(zip_id)
        zfile = zm.get_zip_file_object(zip_id)
        assert zfile is not None
        data = zfile.read("readme.txt")
        assert data == b"Hello World"
        zfile.close()

    @pytest.mark.skipif(not HAS_7Z, reason="py7zr not installed")
    def test_get_file_object_7z(self, sample_7z):
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_7z])
        zip_id = get_zip_file_hash(sample_7z)
        zm.load_zip_file(zip_id)
        zfile = zm.get_zip_file_object(zip_id)
        assert zfile is not None
        data = zfile.read("readme.txt")
        assert data == b"Hello World"
        zfile.close()

    def test_no_password_required_tar(self, sample_tar):
        from src.zip_manager import ZipManager

        zm = ZipManager()
        assert zm.check_zip_requires_password(sample_tar) is False

    def test_mixed_archive_discovery(self, mixed_archive_dir):
        from src.zip_manager import ZipManager

        zm = ZipManager()
        zm.initialize_zip_files([mixed_archive_dir])
        all_archives = zm.get_all_zip_files()
        assert len(all_archives) >= 3

    def test_browse_tar_tree(self, sample_tar):
        """Test building and navigating tree from a tar archive."""
        from src.zip_manager import ZipManager
        from src.utils import get_zip_file_hash

        zm = ZipManager()
        zm.initialize_zip_files([sample_tar])
        zip_id = get_zip_file_hash(sample_tar)
        zm.load_zip_file(zip_id)

        # Root should have readme.txt, docs, images
        root = zm.get_dir_tree(zip_id, "")
        assert root is not None
        assert "readme.txt" in root
        assert "docs" in root
        assert "images" in root

        # Subfolder
        sub = zm.get_dir_tree(zip_id, "images")
        assert sub is not None
        assert "photo.jpg" in sub
