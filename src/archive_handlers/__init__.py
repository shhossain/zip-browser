"""
Unified archive handler package providing a common interface for multiple archive formats.
Supports: ZIP, RAR, 7Z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, GZ, remote ZIP URLs,
and native filesystem directories.
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

# ---------------------------------------------------------------------------
# Extension sets (kept here for backward compatibility)
# ---------------------------------------------------------------------------
ZIP_EXTENSIONS = {".zip", ".iso"}
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


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------
def open_archive(path, *, password=None):
    """Open an archive file (or directory) and return a handler instance.

    Supported sources:
        - Local archive files (ZIP, RAR, 7Z, TAR variants, GZ)
        - Remote ZIP URLs (http/https)
        - Local directories (native filesystem browsing)
    """
    # Remote ZIP
    if is_url(path):
        return RemoteZipHandler(path, password=password)

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
    "open_archive",
    "ArchiveFile",
    "get_archive_ext",
    "is_url",
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
