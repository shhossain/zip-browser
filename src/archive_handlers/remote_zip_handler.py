"""
Handler for remote ZIP files accessed via HTTP/HTTPS using remotezip.
"""
from remotezip import RemoteZip

from .base import BaseArchiveHandler


class RemoteZipHandler(BaseArchiveHandler):
    """Handler for ZIP files at remote URLs (HTTP/HTTPS)."""

    def __init__(self, url, *, password=None):
        super().__init__(url, password=password)
        self._inner = RemoteZip(self._path)
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
