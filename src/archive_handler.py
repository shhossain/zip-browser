"""
Unified archive handler providing a common interface for multiple archive formats.
Supports: ZIP, RAR, 7Z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, GZ, and remote ZIP URLs.
"""

import io
import os
import gzip
import stat
import tarfile
import tempfile
import urllib.parse

import pyzipper
from remotezip import RemoteZip

# Optional imports for additional formats
try:
    import py7zr

    HAS_7Z = True
except ImportError:
    HAS_7Z = False

try:
    import rarfile

    HAS_RAR = True
except ImportError:
    HAS_RAR = False


# Supported archive extensions grouped by handler type
ZIP_EXTENSIONS = {".zip", ".iso"}
TAR_EXTENSIONS = {".tar"}
TAR_GZ_EXTENSIONS = {".tar.gz", ".tgz"}
TAR_BZ2_EXTENSIONS = {".tar.bz2", ".tbz2"}
TAR_XZ_EXTENSIONS = {".tar.xz", ".txz"}
GZ_EXTENSIONS = {".gz"}  # standalone .gz (not .tar.gz)
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

# Glob patterns for discover
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


def is_nested_archive(filename):
    """Check if a filename inside an archive is itself a supported archive.

    Excludes standalone .gz files since they are typically compressed single
    files rather than browsable archives.
    """
    ext = get_archive_ext(filename)
    if ext in GZ_EXTENSIONS and ext not in TAR_GZ_EXTENSIONS:
        return False
    return ext in ALL_ARCHIVE_EXTENSIONS


def _check_format_available(ext):
    """Check if the required library for an extension is available."""
    if ext in SEVENZ_EXTENSIONS and not HAS_7Z:
        raise ImportError(
            "py7zr is required for 7z support. Install it with: pip install py7zr"
        )
    if ext in RAR_EXTENSIONS and not HAS_RAR:
        raise ImportError(
            "rarfile is required for RAR support. Install it with: pip install rarfile"
        )


class ArchiveFile:
    """Unified interface for reading archive files.

    Provides a consistent API regardless of the underlying archive type:
        - namelist() -> list of file paths
        - read(name) -> bytes
        - close()
        - setpassword(pwd_bytes)   (no-op for formats that don't support passwords)
    """

    def __init__(self, path, *, password=None):
        self._path = path
        self._is_remote = is_url(path)
        self._ext = get_archive_ext(path)
        self._password = password
        self._inner = None
        self._names = None  # cached namelist
        self._open()

    # ------------------------------------------------------------------
    # Factory helper
    # ------------------------------------------------------------------
    def _open(self):
        # Remote URLs — only ZIP is supported via RemoteZip
        if self._is_remote:
            self._type = "remote_zip"
            self._inner = RemoteZip(self._path)
            if self._password:
                self._inner.setpassword(self._password)
            return

        ext = self._ext
        _check_format_available(ext)

        if ext in ZIP_EXTENSIONS:
            self._type = "zip"
            self._inner = pyzipper.AESZipFile(self._path, "r")
            if self._password:
                self._inner.setpassword(self._password)

        elif ext in (TAR_EXTENSIONS | TAR_GZ_EXTENSIONS | TAR_BZ2_EXTENSIONS | TAR_XZ_EXTENSIONS):
            self._type = "tar"
            if ext in TAR_GZ_EXTENSIONS:
                mode = "r:gz"
            elif ext in TAR_BZ2_EXTENSIONS:
                mode = "r:bz2"
            elif ext in TAR_XZ_EXTENSIONS:
                mode = "r:xz"
            else:
                mode = "r:"
            self._inner = tarfile.open(self._path, mode)

        elif ext in GZ_EXTENSIONS:
            # Standalone .gz file — expose the single inner file
            self._type = "gz"
            basename = os.path.basename(self._path)
            # Strip .gz to get inner filename
            self._gz_inner_name = basename[:-3] if basename.lower().endswith(".gz") else basename
            # Read into memory so we can serve it
            with gzip.open(self._path, "rb") as f:
                self._gz_data = f.read()

        elif ext in SEVENZ_EXTENSIONS:
            self._type = "7z"
            pwd = self._password.decode("utf-8") if isinstance(self._password, bytes) else self._password
            self._inner = py7zr.SevenZipFile(self._path, "r", password=pwd or None)
            self._7z_cache = {}

        elif ext in RAR_EXTENSIONS:
            self._type = "rar"
            self._inner = rarfile.RarFile(self._path, "r")
            if self._password:
                pwd = self._password.decode("utf-8") if isinstance(self._password, bytes) else self._password
                self._inner.setpassword(pwd)
        else:
            raise ValueError(f"Unsupported archive format: {ext}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def namelist(self):
        """Return a list of all file paths inside the archive."""
        if self._names is not None:
            return self._names

        if self._type in ("zip", "remote_zip"):
            self._names = self._inner.namelist()

        elif self._type == "tar":
            self._names = [m.name + ("/" if m.isdir() else "") for m in self._inner.getmembers()]

        elif self._type == "gz":
            self._names = [self._gz_inner_name]

        elif self._type == "7z":
            self._names = [
                entry.filename + ("/" if entry.is_directory else "")
                for entry in self._inner.list()
            ]

        elif self._type == "rar":
            self._names = [
                info.filename + ("/" if info.is_dir() else "")
                for info in self._inner.infolist()
                if info.filename
            ]

        return self._names

    def read(self, name):
        """Read a file from the archive and return its bytes."""
        if self._type in ("zip", "remote_zip"):
            return self._inner.read(name)

        elif self._type == "tar":
            member = self._inner.getmember(name.rstrip("/"))
            f = self._inner.extractfile(member)
            if f is None:
                raise KeyError(f"Cannot read directory entry: {name}")
            return f.read()

        elif self._type == "gz":
            if name == self._gz_inner_name:
                return self._gz_data
            raise KeyError(f"File not found in gz: {name}")

        elif self._type == "7z":
            if not self._7z_cache:
                self._read_7z_all()
            if name in self._7z_cache:
                return self._7z_cache[name]
            raise KeyError(f"File not found in 7z: {name}")

        elif self._type == "rar":
            return self._inner.read(name)

        raise KeyError(f"Cannot read from archive type: {self._type}")

    def setpassword(self, pwd_bytes):
        """Set password for the archive (bytes)."""
        self._password = pwd_bytes
        if self._type in ("zip", "remote_zip"):
            self._inner.setpassword(pwd_bytes)
        elif self._type == "rar":
            pwd = pwd_bytes.decode("utf-8") if isinstance(pwd_bytes, bytes) else pwd_bytes
            self._inner.setpassword(pwd)
        elif self._type == "7z":
            # py7zr needs to be re-opened with a password
            self.close()
            pwd = pwd_bytes.decode("utf-8") if isinstance(pwd_bytes, bytes) else pwd_bytes
            self._inner = py7zr.SevenZipFile(self._path, "r", password=pwd)
            self._names = None
            self._7z_cache = {}
        # tar/gz don't support passwords — no-op

    def close(self):
        """Close the underlying archive."""
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception:
                pass
            self._inner = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _read_7z_all(self):
        """Extract all files from a 7z archive into cache via temp directory."""
        td = tempfile.mkdtemp()
        try:
            self._inner.extractall(path=td)
            for root, _dirs, files in os.walk(td):
                for fname in files:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, td)
                    # Fix permissions if needed
                    os.chmod(full, stat.S_IRUSR | stat.S_IWUSR)
                    with open(full, "rb") as f:
                        self._7z_cache[rel] = f.read()
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def open_archive(path, *, password=None):
    """Open an archive file and return an ArchiveFile instance."""
    return ArchiveFile(path, password=password)
