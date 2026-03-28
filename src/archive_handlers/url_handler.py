"""
Handler that exposes a web URL as a browsable virtual filesystem.

Non-content pages (HTML) are treated as folders; content URLs (images,
documents, etc.) are treated as files.  Discovery is fully lazy — pages
are only fetched when the user actually visits them.
"""
import urllib.parse

import requests

from .base import BaseArchiveHandler
from .link_parser import extract_links, is_browsable_archive


class UrlHandler(BaseArchiveHandler):
    """Browse a website as if it were an archive.

    Construction is cheap — no HTTP requests are made.  The root page
    (and every subsequent subfolder) is crawled lazily via ``discover()``
    which is called by the browse route when a user navigates into a folder.
    """

    _USER_AGENT = "Mozilla/5.0 (compatible; ZipBrowser/1.0)"

    def __init__(self, url, *, password=None):
        # Normalise: ensure trailing slash for the base URL
        normalised = url if url.endswith("/") else url + "/"
        super().__init__(normalised, password=password)

        self._tree: dict = {}
        self._url_map: dict[str, str] = {}  # tree_path -> absolute URL
        self._discovered: set[str] = set()

        self._session = requests.Session()
        self._session.headers["User-Agent"] = self._USER_AGENT
        if self._password:
            # Support HTTP Basic Auth via "user:pass" password string
            cred = self._password
            if isinstance(cred, bytes):
                cred = cred.decode("utf-8", errors="replace")
            if ":" in cred:
                user, pwd = cred.split(":", 1)
                self._session.auth = (user, pwd)

        # No crawling at construction time — fully deferred to discover().

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def tree(self):
        """The lazily-built directory tree (same structure as *build_zip_tree*)."""
        return self._tree

    def discover(self, path):
        """Crawl the page at *path* and populate its children in the tree.

        Calling this more than once for the same *path* is a no-op.
        Returns the (possibly updated) tree for convenience.
        """
        normalised = path.strip("/")
        if normalised in self._discovered:
            return self._tree

        url = self._url_for_path(normalised)
        try:
            resp = self._session.get(url, timeout=(5, 10), allow_redirects=True,
                                     stream=True)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type.lower():
                resp.close()
                self._discovered.add(normalised)
                return self._tree

            # Read up to 2 MB of HTML in chunks (avoids loading entire
            # response into memory at once).
            max_bytes = 2 * 1024 * 1024
            parts: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                parts.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            resp.close()
            html = b"".join(parts).decode("utf-8", errors="replace")

            # Use the final URL after any redirects.
            # Only keep immediate children (one path-segment deep) so we
            # don't accidentally crawl deeper levels from a single page.
            links = extract_links(html, resp.url)
            for rel_path, link_url, is_file in links:
                # Skip links that are more than one level deep
                if "/" in rel_path.strip("/"):
                    continue
                full_path = (normalised + "/" + rel_path).strip("/")
                self._add_entry(full_path, link_url, is_file)
        except Exception:
            pass  # network errors are non-fatal; the folder stays empty

        self._discovered.add(normalised)
        self._names = None  # invalidate cached namelist
        return self._tree

    def namelist(self):
        if self._names is None:
            self._names = self._collect_paths()
        return self._names

    def read(self, name):
        """Download a file by streaming it in chunks."""
        name = name.strip("/")
        url = self._url_map.get(name)
        if not url:
            url = self._path + urllib.parse.quote(name, safe="/")

        resp = self._session.get(url, timeout=30, allow_redirects=True,
                                 stream=True)
        resp.raise_for_status()

        chunks: list[bytes] = []
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            chunks.append(chunk)
        resp.close()

        return b"".join(chunks)

    def get_url(self, name):
        """Return the direct URL for a file entry.

        This allows callers (e.g. video routes, view routes) to stream or
        redirect to the URL directly instead of downloading through ``read()``.
        """
        name = name.strip("/")
        url = self._url_map.get(name)
        if url:
            return url
        return self._path + urllib.parse.quote(name, safe="/")

    def close(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url_for_path(self, path):
        """Map a tree path back to an absolute URL."""
        if not path:
            return self._path
        if path in self._url_map:
            url = self._url_map[path]
            # For folders ensure a trailing slash so relative resolution works
            if not self._is_file_entry(path):
                url = url if url.endswith("/") else url + "/"
            return url
        return self._path + path + "/"

    def _is_file_entry(self, path):
        """Return True if *path* resolves to a file (``None``) in the tree."""
        parts = path.strip("/").split("/")
        node = self._tree
        for p in parts[:-1]:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return False
        if not parts:
            return False
        val = node.get(parts[-1])
        return val is None or val == "__archive__"

    def _add_entry(self, path, url, is_file):
        """Insert an entry into the tree and record its URL."""
        parts = path.strip("/").split("/")
        if not parts or not parts[0]:
            return

        node = self._tree
        for part in parts[:-1]:
            if part not in node or not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]

        leaf = parts[-1]
        if is_file:
            # Don't overwrite an already-populated folder
            if leaf not in node or not isinstance(node.get(leaf), dict):
                if is_browsable_archive(leaf):
                    node[leaf] = "__archive__"
                else:
                    node[leaf] = None
        else:
            if leaf not in node or not isinstance(node.get(leaf), dict):
                node[leaf] = {}

        self._url_map[path] = url

    def _ensure_node(self, path):
        """Navigate (or create) the tree node for *path*."""
        if not path:
            return self._tree
        parts = path.strip("/").split("/")
        node = self._tree
        for p in parts:
            if p not in node or not isinstance(node[p], dict):
                node[p] = {}
            node = node[p]
        return node

    def _collect_paths(self):
        """Flatten the tree into a sorted list of paths (namelist-compatible)."""
        paths: list[str] = []

        def _walk(node, prefix):
            for name in sorted(node):
                value = node[name]
                full = prefix + name
                if isinstance(value, dict):
                    paths.append(full + "/")
                    _walk(value, full + "/")
                else:
                    paths.append(full)

        _walk(self._tree, "")
        return paths
