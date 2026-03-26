"""
Handler for RAR archives using rarfile.
"""
from .base import BaseArchiveHandler

try:
    import rarfile

    HAS_RAR = True
except ImportError:
    HAS_RAR = False


def check_rar_available():
    if not HAS_RAR:
        raise ImportError(
            "rarfile is required for RAR support. Install it with: pip install rarfile"
        )


class RarArchiveHandler(BaseArchiveHandler):
    """Handler for .rar files."""

    def __init__(self, path, *, password=None):
        check_rar_available()
        super().__init__(path, password=password)
        self._inner = rarfile.RarFile(self._path, "r")
        if self._password:
            pwd = self._decode_password(self._password)
            self._inner.setpassword(pwd)

    def namelist(self):
        if self._names is None:
            self._names = [
                info.filename + ("/" if info.is_dir() else "")
                for info in self._inner.infolist()
                if info.filename
            ]
        return self._names

    def read(self, name):
        return self._inner.read(name)

    def setpassword(self, pwd_bytes):
        self._password = pwd_bytes
        pwd = self._decode_password(pwd_bytes)
        self._inner.setpassword(pwd)

    def close(self):
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception:
                pass
            self._inner = None

    @staticmethod
    def _decode_password(pwd):
        if isinstance(pwd, bytes):
            return pwd.decode("utf-8")
        return pwd
