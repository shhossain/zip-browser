"""
Unified archive handler package providing a common interface for multiple archive formats.
Supports: ZIP, RAR, 7Z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, GZ, remote ZIP URLs,
native filesystem directories, and browsable web URLs.
"""

import os
import urllib.parse

from .base import BaseArchiveHandler
from .zip_handler import ZipArchiveHandler
from .tar_handler import TarArchiveHandler
from .gz_handler import GzArchiveHandler
from .sevenz_handler import SevenZArchiveHandler
from .rar_handler import RarArchiveHandler
from .remote_zip_handler import RemoteZipHandler
from .filesystem_handler import FilesystemHandler
from .url_handler import UrlHandler
from .torrent_handler import TorrentHandler, is_magnet

# ---------------------------------------------------------------------------
# Extension sets (kept here for backward compatibility)
# ---------------------------------------------------------------------------
ZIP_EXTENSIONS = {".zip", ".iso"}
TORRENT_EXTENSIONS = {".torrent"}
TAR_EXTENSIONS = {".tar"}
TAR_GZ_EXTENSIONS = {".tar.gz", ".tgz"}
TAR_BZ2_EXTENSIONS = {".tar.bz2", ".tbz2"}
TAR_XZ_EXTENSIONS = {".tar.xz", ".txz"}
GZ_EXTENSIONS = {".gz"}
SEVENZ_EXTENSIONS = {".7z"}
RAR_EXTENSIONS = {".rar"}

ALL_ARCHIVE_EXTENSIONS = (
    ZIP_EXTENSIONS
    | TAR_EXTENSIONS
    | TAR_GZ_EXTENSIONS
    | TAR_BZ2_EXTENSIONS
    | TAR_XZ_EXTENSIONS
    | GZ_EXTENSIONS
    | SEVENZ_EXTENSIONS
    | RAR_EXTENSIONS
    | TORRENT_EXTENSIONS
)

ARCHIVE_GLOB_PATTERNS = [
    "*.zip",
    "*.iso",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.tar.bz2",
    "*.tbz2",
    "*.tar.xz",
    "*.txz",
    "*.gz",
    "*.7z",
    "*.rar",
    "*.torrent",
]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def get_archive_ext(filepath):
    """Get the archive extension, handling compound extensions like .tar.gz."""
    lower = filepath.lower()
    for compound in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lower.endswith(compound):
            return compound
    return os.path.splitext(lower)[1]


def is_url(path):
    """Check if a path is a URL."""
    try:
        result = urllib.parse.urlparse(path)
        return all([result.scheme, result.netloc]) and result.scheme in ["http", "https"]
    except Exception:
        return False


def is_supported_archive(filepath):
    """Check if a file path has a supported archive extension."""
    return get_archive_ext(filepath) in ALL_ARCHIVE_EXTENSIONS


def is_directory(path):
    """Check if a path is an existing directory (for filesystem browsing)."""
    return os.path.isdir(path)


def is_nested_archive(filename):
    """Check if a filename inside an archive is itself a supported archive.

    Excludes standalone .gz files since they are typically compressed single
    files rather than browsable archives.
    """
    ext = get_archive_ext(filename)
    if ext in GZ_EXTENSIONS and ext not in TAR_GZ_EXTENSIONS:
        return False
    return ext in ALL_ARCHIVE_EXTENSIONS


def is_archive_url(url):
    """Check if a URL points to a known archive file (by extension).

    ``.torrent`` URLs are excluded because they need the TorrentHandler
    (downloaded first, then parsed), not the RemoteZipHandler.
    """
    if not is_url(url):
        return False
    path = urllib.parse.urlparse(url).path.lower()
    ext = get_archive_ext(path)
    if ext in TORRENT_EXTENSIONS:
        return False
    return ext in ALL_ARCHIVE_EXTENSIONS


# ---------------------------------------------------------------------------
# Handler registry — maps extension to handler class
# ---------------------------------------------------------------------------
_HANDLER_MAP = {}
for _ext in ZIP_EXTENSIONS:
    _HANDLER_MAP[_ext] = ZipArchiveHandler
for _ext in (TAR_EXTENSIONS | TAR_GZ_EXTENSIONS | TAR_BZ2_EXTENSIONS | TAR_XZ_EXTENSIONS):
    _HANDLER_MAP[_ext] = TarArchiveHandler
for _ext in GZ_EXTENSIONS:
    _HANDLER_MAP[_ext] = GzArchiveHandler
for _ext in SEVENZ_EXTENSIONS:
    _HANDLER_MAP[_ext] = SevenZArchiveHandler
for _ext in RAR_EXTENSIONS:
    _HANDLER_MAP[_ext] = RarArchiveHandler
for _ext in TORRENT_EXTENSIONS:
    _HANDLER_MAP[_ext] = TorrentHandler


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------
def _download_and_open_torrent(url, *, password=None):
    """Download a remote .torrent file to a temp path and open it."""
    import tempfile
    import requests

    resp = requests.get(url, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    tmp_dir = tempfile.mkdtemp(prefix="zipbrowser_torrent_dl_")
    filename = os.path.basename(urllib.parse.urlparse(url).path) or "remote.torrent"
    tmp_path = os.path.join(tmp_dir, filename)
    with open(tmp_path, "wb") as f:
        f.write(resp.content)

    return TorrentHandler(tmp_path, password=password)


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------
def open_archive(path, *, password=None):
    """Open an archive file (or directory) and return a handler instance.

    Supported sources:
        - Local archive files (ZIP, RAR, 7Z, TAR variants, GZ)
        - Remote archive URLs (http/https pointing to archive files)
        - Remote web URLs (http/https pages browsed as virtual filesystems)
        - Local directories (native filesystem browsing)
    """
    # Magnet URL
    if is_magnet(path):
        return TorrentHandler(path, password=password)

    # Remote URL
    if is_url(path):
        # .torrent URLs: download to temp file, then open with TorrentHandler
        url_path = urllib.parse.urlparse(path).path.lower()
        if get_archive_ext(url_path) in TORRENT_EXTENSIONS:
            return _download_and_open_torrent(path, password=password)
        if is_archive_url(path):
            return RemoteZipHandler(path, password=password)
        return UrlHandler(path, password=password)

    # Native filesystem directory
    if os.path.isdir(path):
        return FilesystemHandler(path, password=password)

    # Local archive file
    ext = get_archive_ext(path)
    handler_cls = _HANDLER_MAP.get(ext)
    if handler_cls is None:
        raise ValueError(f"Unsupported archive format: {ext}")

    # TarArchiveHandler needs the ext to pick the right mode
    if handler_cls is TarArchiveHandler:
        return handler_cls(path, password=password, ext=ext)

    return handler_cls(path, password=password)


# Backward-compatible alias
ArchiveFile = open_archive


__all__ = [
    "BaseArchiveHandler",
    "ZipArchiveHandler",
    "TarArchiveHandler",
    "GzArchiveHandler",
    "SevenZArchiveHandler",
    "RarArchiveHandler",
    "RemoteZipHandler",
    "FilesystemHandler",
    "UrlHandler",
    "open_archive",
    "ArchiveFile",
    "get_archive_ext",
    "is_url",
    "is_archive_url",
    "is_supported_archive",
    "is_directory",
    "is_nested_archive",
    "ARCHIVE_GLOB_PATTERNS",
    "ALL_ARCHIVE_EXTENSIONS",
    "ZIP_EXTENSIONS",
    "TAR_EXTENSIONS",
    "TAR_GZ_EXTENSIONS",
    "TAR_BZ2_EXTENSIONS",
    "TAR_XZ_EXTENSIONS",
    "GZ_EXTENSIONS",
    "SEVENZ_EXTENSIONS",
    "RAR_EXTENSIONS",
]
