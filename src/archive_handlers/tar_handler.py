"""
Handler for TAR archives (plain, gzip, bzip2, xz).
"""
import tarfile

from .base import BaseArchiveHandler


# Extension -> tarfile mode mapping
_TAR_MODES = {
    ".tar": "r:",
    ".tar.gz": "r:gz",
    ".tgz": "r:gz",
    ".tar.bz2": "r:bz2",
    ".tbz2": "r:bz2",
    ".tar.xz": "r:xz",
    ".txz": "r:xz",
}


class TarArchiveHandler(BaseArchiveHandler):
    """Handler for .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz files."""

    def __init__(self, path, *, password=None, ext=None):
        super().__init__(path, password=password)
        mode = _TAR_MODES.get(ext, "r:")
        self._inner = tarfile.open(self._path, mode)

    def namelist(self):
        if self._names is None:
            self._names = [
                m.name + ("/" if m.isdir() else "") for m in self._inner.getmembers()
            ]
        return self._names

    def read(self, name):
        member = self._inner.getmember(name.rstrip("/"))
        f = self._inner.extractfile(member)
        if f is None:
            raise KeyError(f"Cannot read directory entry: {name}")
        return f.read()

    def close(self):
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception:
                pass
            self._inner = None
