"""
Abstract base class for all archive handlers.
"""
from abc import ABC, abstractmethod


class BaseArchiveHandler(ABC):
    """Abstract interface for reading archive-like sources.

    Every handler must implement:
        - namelist() -> list[str]
        - read(name) -> bytes
        - close()

    Optional overrides:
        - setpassword(pwd_bytes)  (default is no-op)
    """

    def __init__(self, path, *, password=None):
        self._path = path
        self._password = password
        self._names = None  # cached namelist

    @abstractmethod
    def namelist(self) -> list[str]:
        """Return a list of all entry paths inside the archive/source."""

    @abstractmethod
    def read(self, name: str) -> bytes:
        """Read a file entry and return its bytes."""

    @abstractmethod
    def close(self):
        """Release the underlying resource."""

    def setpassword(self, pwd_bytes: bytes):
        """Set password for the archive. No-op by default."""

    # Context manager support
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
