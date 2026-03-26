"""
Handler for 7z archives using py7zr.
"""
import os
import stat
import tempfile

from .base import BaseArchiveHandler

try:
    import py7zr

    HAS_7Z = True
except ImportError:
    HAS_7Z = False


def check_7z_available():
    if not HAS_7Z:
        raise ImportError(
            "py7zr is required for 7z support. Install it with: pip install py7zr"
        )


class SevenZArchiveHandler(BaseArchiveHandler):
    """Handler for .7z files."""

    def __init__(self, path, *, password=None):
        check_7z_available()
        super().__init__(path, password=password)
        pwd = self._decode_password(password)
        self._inner = py7zr.SevenZipFile(self._path, "r", password=pwd or None)
        self._cache = {}

    def namelist(self):
        if self._names is None:
            self._names = [
                entry.filename + ("/" if entry.is_directory else "")
                for entry in self._inner.list()
            ]
        return self._names

    def read(self, name):
        if not self._cache:
            self._extract_all()
        if name in self._cache:
            return self._cache[name]
        raise KeyError(f"File not found in 7z: {name}")

    def setpassword(self, pwd_bytes):
        self._password = pwd_bytes
        self.close()
        pwd = self._decode_password(pwd_bytes)
        self._inner = py7zr.SevenZipFile(self._path, "r", password=pwd)
        self._names = None
        self._cache = {}

    def close(self):
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception:
                pass
            self._inner = None

    def _extract_all(self):
        """Extract all files from the 7z archive into an in-memory cache."""
        td = tempfile.mkdtemp()
        try:
            self._inner.extractall(path=td)
            for root, _dirs, files in os.walk(td):
                for fname in files:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, td)
                    os.chmod(full, stat.S_IRUSR | stat.S_IWUSR)
                    with open(full, "rb") as f:
                        self._cache[rel] = f.read()
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    @staticmethod
    def _decode_password(pwd):
        if isinstance(pwd, bytes):
            return pwd.decode("utf-8")
        return pwd
