"""
Handler that exposes a native filesystem directory as a browsable archive.
"""
import os

from .base import BaseArchiveHandler


class FilesystemHandler(BaseArchiveHandler):
    """Treats a local directory as a read-only archive.

    - namelist() returns all relative paths under the directory.
    - read(name) returns the bytes of the file at that relative path.
    """

    def __init__(self, path, *, password=None):
        super().__init__(os.path.abspath(path), password=password)
        if not os.path.isdir(self._path):
            raise NotADirectoryError(f"Not a directory: {self._path}")
        # Verify we can actually list the directory (macOS TCC may block this)
        try:
            os.listdir(self._path)
        except OSError as e:
            raise PermissionError(
                f"Cannot read directory (permission denied): {self._path}. "
                f"On macOS, grant Full Disk Access to your terminal app "
                f"in System Settings > Privacy & Security > Full Disk Access."
            ) from e

    def namelist(self):
        if self._names is None:
            self._names = self._walk()
        return self._names

    def read(self, name):
        full = self._resolve(name)
        if os.path.isdir(full):
            raise KeyError(f"Cannot read directory entry: {name}")
        with open(full, "rb") as f:
            return f.read()

    def close(self):
        pass  # nothing to release

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _walk(self):
        """Walk the directory tree and return relative paths."""
        entries = []
        for root, dirs, files in os.walk(self._path):
            rel_root = os.path.relpath(root, self._path)
            if rel_root == ".":
                rel_root = ""
            # Add directories (with trailing /)
            for d in sorted(dirs):
                rel = os.path.join(rel_root, d) if rel_root else d
                entries.append(rel + "/")
            # Add files
            for f in sorted(files):
                rel = os.path.join(rel_root, f) if rel_root else f
                entries.append(rel)
        return entries

    def _resolve(self, name):
        """Resolve a relative name to a full path, preventing path traversal."""
        full = os.path.normpath(os.path.join(self._path, name))
        # Prevent directory traversal
        if not full.startswith(self._path):
            raise ValueError(f"Path traversal detected: {name}")
        if not os.path.exists(full):
            raise KeyError(f"File not found: {name}")
        return full
