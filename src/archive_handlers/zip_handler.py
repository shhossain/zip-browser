"""
Handler for ZIP and ISO archives using pyzipper.
"""
import pyzipper

from .base import BaseArchiveHandler


class ZipArchiveHandler(BaseArchiveHandler):
    """Handler for .zip and .iso files."""

    def __init__(self, path, *, password=None):
        super().__init__(path, password=password)
        self._inner = pyzipper.AESZipFile(self._path, "r")
        if self._password:
            self._inner.setpassword(self._password)

    def namelist(self):
        if self._names is None:
            self._names = self._inner.namelist()
        return self._names

    def read(self, name):
        return self._inner.read(name)

    def setpassword(self, pwd_bytes):
        self._password = pwd_bytes
        self._inner.setpassword(pwd_bytes)

    def close(self):
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception:
                pass
            self._inner = None
