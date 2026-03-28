"""
HTML link extraction and URL classification for web browsing.
"""

import os
import urllib.parse
from html.parser import HTMLParser


# File extensions that indicate downloadable content (treated as files)
CONTENT_EXTENSIONS = {
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico",
    ".tiff", ".tif", ".avif",
    # Videos
    ".mp4", ".webm", ".ogg", ".mov", ".avi", ".mkv", ".m4v", ".wmv",
    ".flv", ".3gp",
    # Audio
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".wma",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".epub",
    # Archives
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz",
    ".tbz2", ".txz", ".iso",
    # Data / text
    ".txt", ".csv", ".json", ".xml", ".yaml", ".yml", ".log",
    ".md", ".rst",
    # Executables & packages
    ".exe", ".msi", ".dmg", ".deb", ".rpm", ".apk", ".appimage",
    # Torrents
    ".torrent",
}

# Web resource extensions to skip entirely
_SKIP_EXTENSIONS = {".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".map"}

# Archive extensions that can be browsed as nested archives
_BROWSABLE_ARCHIVE_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".iso", ".tgz", ".tbz2", ".txz",
}
_COMPOUND_ARCHIVE_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz")


class _LinkExtractor(HTMLParser):
    """Extract href values from <a> tags in HTML."""

    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for attr, value in attrs:
                if attr == "href" and value:
                    self.links.append(value)

    def error(self, message):
        pass


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------

def classify_url(url):
    """Classify a URL as ``'file'``, ``'folder'``, or ``'skip'``."""
    path = urllib.parse.urlparse(url).path.rstrip("/")
    ext = os.path.splitext(path.lower())[1]
    if ext in CONTENT_EXTENSIONS:
        return "file"
    if ext in _SKIP_EXTENSIONS:
        return "skip"
    return "folder"


def is_browsable_archive(filename):
    """Check if a filename is a browsable archive (for nested archive support).

    Standalone ``.gz`` files are excluded since they are not browsable.
    """
    lower = filename.lower()
    for suffix in _COMPOUND_ARCHIVE_SUFFIXES:
        if lower.endswith(suffix):
            return True
    ext = os.path.splitext(lower)[1]
    if ext == ".gz":
        return False
    return ext in _BROWSABLE_ARCHIVE_EXTENSIONS


def extract_links(html, page_url):
    """Extract and classify links from an HTML page.

    Returns a list of ``(rel_path, abs_url, is_file)`` tuples.
    Only same-origin links are returned; web resources are skipped.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        return []

    page_parsed = urllib.parse.urlparse(page_url)
    # Ensure the page URL ends with '/' for correct relative resolution
    if not page_url.endswith("/"):
        base_for_join = page_url.rsplit("/", 1)[0] + "/"
    else:
        base_for_join = page_url

    seen: set[str] = set()
    results: list[tuple[str, str, bool]] = []

    for href in parser.links:
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "data:", "tel:")):
            continue

        resolved = urllib.parse.urljoin(base_for_join, href)
        resolved_parsed = urllib.parse.urlparse(resolved)

        # Same origin only
        if resolved_parsed.netloc != page_parsed.netloc:
            continue
        if resolved_parsed.scheme not in ("http", "https"):
            continue

        # Canonical URL (strip query & fragment)
        clean_url = urllib.parse.urlunparse((
            resolved_parsed.scheme, resolved_parsed.netloc,
            resolved_parsed.path, "", "", "",
        ))

        if clean_url in seen or clean_url.rstrip("/") == page_url.rstrip("/"):
            continue
        seen.add(clean_url)

        kind = classify_url(clean_url)
        if kind == "skip":
            continue

        rel = _relative_path(clean_url, page_url)
        if not rel:
            continue

        results.append((rel, clean_url, kind == "file"))

    return results


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _relative_path(link_url, page_url):
    """Return *link_url*'s path relative to *page_url* (no leading/trailing slashes)."""
    link_path = urllib.parse.unquote(urllib.parse.urlparse(link_url).path)
    page_path = urllib.parse.unquote(urllib.parse.urlparse(page_url).path)

    if not page_path.endswith("/"):
        page_path = page_path.rsplit("/", 1)[0] + "/"

    if link_path.startswith(page_path):
        rel = link_path[len(page_path):]
    else:
        # Outside the page's subtree — use the last path segment
        rel = link_path.rstrip("/").rsplit("/", 1)[-1]

    return rel.strip("/")
