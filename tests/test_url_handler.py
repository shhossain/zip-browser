"""
Tests for the URL handler and link parser modules.
"""
import os
import tempfile
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

from src.archive_handlers.link_parser import (
    classify_url,
    extract_links,
    is_browsable_archive,
)
from src.archive_handlers.url_handler import UrlHandler
from src.archive_handlers import open_archive, is_url, is_archive_url
from src.zip_manager import ZipManager


# =====================================================================
# link_parser tests
# =====================================================================

class TestClassifyUrl:
    def test_file_urls(self):
        assert classify_url("https://example.com/photo.jpg") == "file"
        assert classify_url("https://example.com/doc.pdf") == "file"
        assert classify_url("https://example.com/video.mp4") == "file"
        assert classify_url("https://example.com/archive.zip") == "file"
        assert classify_url("https://example.com/data.csv") == "file"

    def test_folder_urls(self):
        assert classify_url("https://example.com/page/") == "folder"
        assert classify_url("https://example.com/about") == "folder"
        assert classify_url("https://example.com/") == "folder"

    def test_skip_urls(self):
        assert classify_url("https://example.com/style.css") == "skip"
        assert classify_url("https://example.com/app.js") == "skip"
        assert classify_url("https://example.com/font.woff2") == "skip"


class TestIsBrowsableArchive:
    def test_browsable(self):
        assert is_browsable_archive("test.zip") is True
        assert is_browsable_archive("test.rar") is True
        assert is_browsable_archive("test.7z") is True
        assert is_browsable_archive("test.tar.gz") is True
        assert is_browsable_archive("test.tar.bz2") is True
        assert is_browsable_archive("test.tar.xz") is True
        assert is_browsable_archive("test.iso") is True

    def test_not_browsable(self):
        assert is_browsable_archive("test.gz") is False
        assert is_browsable_archive("test.txt") is False
        assert is_browsable_archive("test.jpg") is False


class TestExtractLinks:
    def test_basic_extraction(self):
        html = """
        <html><body>
        <a href="docs/">Docs</a>
        <a href="image.jpg">Image</a>
        </body></html>
        """
        links = extract_links(html, "https://example.com/files/")
        names = {name for name, _, _ in links}
        assert "docs" in names
        assert "image.jpg" in names

    def test_file_classification(self):
        html = '<a href="photo.png">Photo</a><a href="subdir/">Subdir</a>'
        links = extract_links(html, "https://example.com/")
        by_name = {name: is_file for name, _, is_file in links}
        assert by_name["photo.png"] is True
        assert by_name["subdir"] is False

    def test_external_links_filtered(self):
        html = '<a href="https://other.com/page">External</a><a href="local/">Local</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 1
        assert links[0][0] == "local"

    def test_skips_css_js(self):
        html = '<a href="style.css">CSS</a><a href="app.js">JS</a><a href="page/">Page</a>'
        links = extract_links(html, "https://example.com/")
        names = {name for name, _, _ in links}
        assert "style.css" not in names
        assert "app.js" not in names
        assert "page" in names

    def test_ignores_fragments_and_javascript(self):
        html = '<a href="#top">Top</a><a href="javascript:void(0)">JS</a><a href="real/">Real</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 1
        assert links[0][0] == "real"

    def test_self_link_excluded(self):
        html = '<a href="./">Self</a><a href="other/">Other</a>'
        links = extract_links(html, "https://example.com/page/")
        names = {name for name, _, _ in links}
        assert "other" in names

    def test_archive_link_classified_as_file(self):
        html = '<a href="data.zip">Archive</a>'
        links = extract_links(html, "https://example.com/")
        assert links[0][2] is True  # is_file


# =====================================================================
# is_archive_url tests
# =====================================================================

class TestIsArchiveUrl:
    def test_archive_urls(self):
        assert is_archive_url("https://example.com/file.zip") is True
        assert is_archive_url("https://example.com/file.tar.gz") is True
        assert is_archive_url("https://example.com/file.7z") is True

    def test_non_archive_urls(self):
        assert is_archive_url("https://example.com/page") is False
        assert is_archive_url("https://example.com/image.jpg") is False
        assert is_archive_url("https://example.com/") is False

    def test_non_url(self):
        assert is_archive_url("/local/path.zip") is False


# =====================================================================
# UrlHandler tests (using a local HTTP server)
# =====================================================================

# Serve a small directory of HTML and files for testing
_TEST_HTML_ROOT = """<!doctype html>
<html><body>
<a href="subdir/">Subdir</a>
<a href="hello.txt">Hello Text</a>
<a href="image.png">Image</a>
</body></html>
"""

_TEST_HTML_SUBDIR = """<!doctype html>
<html><body>
<a href="nested.txt">Nested File</a>
<a href="archive.zip">Archive</a>
</body></html>
"""


class _TestRequestHandler(SimpleHTTPRequestHandler):
    """Minimal handler that serves canned HTML pages and files."""

    _ROUTES = {
        "/": ("text/html", _TEST_HTML_ROOT.encode()),
        "/subdir/": ("text/html", _TEST_HTML_SUBDIR.encode()),
        "/hello.txt": ("text/plain", b"Hello, world!"),
        "/image.png": ("image/png", b"\x89PNG fake image data"),
        "/subdir/nested.txt": ("text/plain", b"I am nested"),
        "/subdir/archive.zip": ("application/zip", b"PK fake zip"),
    }

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in self._ROUTES:
            content_type, body = self._ROUTES[path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # suppress logs


@pytest.fixture(scope="module")
def local_server():
    """Start a local HTTP server in a background thread."""
    server = HTTPServer(("127.0.0.1", 0), _TestRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestUrlHandler:
    def test_construction_is_instant(self, local_server):
        """UrlHandler.__init__ should not make HTTP requests."""
        handler = UrlHandler(local_server)
        assert handler.tree == {}
        assert handler.namelist() == []
        handler.close()

    def test_discover_root(self, local_server):
        handler = UrlHandler(local_server)
        handler.discover("")
        tree = handler.tree
        assert "subdir" in tree
        assert isinstance(tree["subdir"], dict)
        assert "hello.txt" in tree
        assert tree["hello.txt"] is None
        assert "image.png" in tree
        handler.close()

    def test_discover_subdir(self, local_server):
        handler = UrlHandler(local_server)
        handler.discover("")
        handler.discover("subdir")
        subdir = handler.tree["subdir"]
        assert "nested.txt" in subdir
        assert subdir["nested.txt"] is None
        # archive.zip should be marked as __archive__
        assert subdir["archive.zip"] == "__archive__"
        handler.close()

    def test_discover_idempotent(self, local_server):
        handler = UrlHandler(local_server)
        handler.discover("")
        tree1 = dict(handler.tree)
        handler.discover("")
        tree2 = dict(handler.tree)
        assert tree1 == tree2
        handler.close()

    def test_read_file(self, local_server):
        handler = UrlHandler(local_server)
        handler.discover("")
        data = handler.read("hello.txt")
        assert data == b"Hello, world!"
        handler.close()

    def test_read_nested_file(self, local_server):
        handler = UrlHandler(local_server)
        handler.discover("")
        handler.discover("subdir")
        data = handler.read("subdir/nested.txt")
        assert data == b"I am nested"
        handler.close()

    def test_get_url(self, local_server):
        handler = UrlHandler(local_server)
        handler.discover("")
        url = handler.get_url("hello.txt")
        assert url.startswith("http://")
        assert "hello.txt" in url
        handler.close()

    def test_namelist(self, local_server):
        handler = UrlHandler(local_server)
        handler.discover("")
        names = handler.namelist()
        assert "hello.txt" in names
        assert "image.png" in names
        assert "subdir/" in names
        handler.close()

    def test_open_archive_returns_url_handler(self, local_server):
        handler = open_archive(local_server)
        assert isinstance(handler, UrlHandler)
        handler.close()

    def test_password_basic_auth(self, local_server):
        """Password with ':' should be parsed as basic auth credentials."""
        handler = UrlHandler(local_server, password="user:pass")
        assert handler._session.auth == ("user", "pass")
        handler.close()


# =====================================================================
# ZipManager URL integration tests
# =====================================================================

class TestZipManagerUrl:
    def test_read_url_shortcut(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".url", delete=False
        ) as f:
            f.write("[InternetShortcut]\nURL=https://example.com/files/\n")
            path = f.name
        try:
            url = ZipManager.read_url_shortcut(path)
            assert url == "https://example.com/files/"
        finally:
            os.unlink(path)

    def test_read_url_shortcut_invalid(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".url", delete=False
        ) as f:
            f.write("garbage content\n")
            path = f.name
        try:
            url = ZipManager.read_url_shortcut(path)
            assert url is None
        finally:
            os.unlink(path)

    def test_discover_url_file(self):
        zm = ZipManager()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".url", delete=False
        ) as f:
            f.write("[InternetShortcut]\nURL=https://example.com/\n")
            path = f.name
        try:
            result = zm.discover_zip_files(path)
            assert result == ["https://example.com/"]
        finally:
            os.unlink(path)

    def test_discover_url_files_in_directory(self, tmp_path):
        url_file = tmp_path / "site.url"
        url_file.write_text("[InternetShortcut]\nURL=https://example.com/data/\n")
        zm = ZipManager()
        results = zm.discover_zip_files(str(tmp_path))
        urls = [r for r in results if r.startswith("https://")]
        assert "https://example.com/data/" in urls

    def test_initialize_web_url(self, local_server):
        zm = ZipManager()
        zm.initialize_zip_files([local_server])
        assert len(zm.zip_files) == 1
        zip_id = list(zm.zip_files.keys())[0]
        info = zm.zip_files[zip_id]
        assert info["is_remote"] is True
        assert info["requires_password"] is False

    def test_load_and_browse_web_url(self, local_server):
        zm = ZipManager()
        zm.initialize_zip_files([local_server])
        zip_id = list(zm.zip_files.keys())[0]
        zm.load_zip_file(zip_id)

        # Get root tree (triggers discover)
        root = zm.get_dir_tree(zip_id, "")
        assert root is not None
        assert "hello.txt" in root
        assert "subdir" in root

    def test_get_file_url(self, local_server):
        zm = ZipManager()
        zm.initialize_zip_files([local_server])
        zip_id = list(zm.zip_files.keys())[0]
        zm.load_zip_file(zip_id)
        zm.get_dir_tree(zip_id, "")  # trigger discover

        url = zm.get_file_url(zip_id, "hello.txt")
        assert url is not None
        assert "hello.txt" in url

    def test_get_file_url_non_url_handler(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        zm = ZipManager()
        zm.initialize_zip_files([str(tmp_path)])
        zip_id = list(zm.zip_files.keys())[0]
        zm.load_zip_file(zip_id)
        # Filesystem handler should return None
        assert zm.get_file_url(zip_id, "test.txt") is None
