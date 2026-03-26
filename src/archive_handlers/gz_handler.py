"""
Handler for standalone .gz files (not .tar.gz).
"""
import gzip
import os

from .base import BaseArchiveHandler


class GzArchiveHandler(BaseArchiveHandler):
    """Handler for standalone .gz files."""

    def __init__(self, path, *, password=None):
        super().__init__(path, password=password)
        basename = os.path.basename(self._path)
        self._inner_name = basename[:-3] if basename.lower().endswith(".gz") else basename
        with gzip.open(self._path, "rb") as f:
            self._data = f.read()

    def namelist(self):
        if self._names is None:
            self._names = [self._inner_name]
        return self._names

    def read(self, name):
        if name == self._inner_name:
            return self._data
        raise KeyError(f"File not found in gz: {name}")

    def close(self):
        self._data = None
