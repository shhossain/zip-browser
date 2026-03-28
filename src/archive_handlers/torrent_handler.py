"""
Handler for .torrent files and magnet: URLs.

Browsing (file tree) requires ``torrentool`` (parses .torrent metadata).
Magnet URL resolution and streaming individual files requires ``libtorrent``.
"""

import os
import tempfile
import time

from .base import BaseArchiveHandler


def is_magnet(url: str) -> bool:
    """Return *True* if *url* is a BitTorrent magnet link."""
    return isinstance(url, str) and url.strip().lower().startswith("magnet:")


class TorrentHandler(BaseArchiveHandler):
    """Browse torrent contents without downloading the full data.

    For ``.torrent`` files the file tree is extracted purely from the
    bencoded metadata — no network access is required.

    For ``magnet:`` URLs the handler uses *libtorrent* to fetch only the
    metadata (~KB) from DHT / trackers, then exposes the same tree.

    Reading individual file contents (``read()``) requires *libtorrent*
    and will selectively download only the pieces for the requested file.
    """

    def __init__(self, path, *, password=None):
        super().__init__(path, password=password)
        self._tree: dict = {}
        self._file_sizes: dict[str, int] = {}
        self._torrent_name: str = ""
        self._lt_session = None
        self._lt_handle = None
        self._save_path: str | None = None
        self._torrent_info = None
        self._torrent_file_path: str | None = None

        if is_magnet(path):
            self._init_magnet(path)
        else:
            self._init_torrent_file(path)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_torrent_file(self, path):
        """Parse .torrent file metadata (no network needed)."""
        from torrentool.api import Torrent

        torrent = Torrent.from_file(path)
        self._torrent_name = torrent.name or os.path.basename(path)

        if torrent.files:
            for tf in torrent.files:
                # TorrentFile namedtuple: .name (path str), .length (int)
                norm = tf.name.replace("\\", "/").strip("/")
                self._file_sizes[norm] = tf.length
                self._insert_into_tree(norm)
        elif hasattr(torrent, "name") and hasattr(torrent, "total_size"):
            # Single-file torrent
            self._file_sizes[torrent.name] = torrent.total_size
            self._insert_into_tree(torrent.name)

        self._torrent_file_path = path

    def _init_magnet(self, magnet_url):
        """Resolve magnet link via libtorrent to obtain metadata."""
        try:
            import libtorrent as lt
        except ImportError:
            raise ImportError(
                "The 'libtorrent' package is required for magnet URLs. "
                "Install it with: pip install libtorrent"
            )

        self._save_path = tempfile.mkdtemp(prefix="zipbrowser_torrent_")

        settings = {
            "enable_dht": True,
            "enable_lsd": True,
            "enable_natpmp": True,
            "enable_upnp": True,
        }
        self._lt_session = lt.session(settings)
        self._lt_session.add_dht_router("router.bittorrent.com", 6881)
        self._lt_session.add_dht_router("router.utorrent.com", 6881)
        self._lt_session.add_dht_router("dht.transmissionbt.com", 6881)

        params = lt.parse_magnet_uri(magnet_url)
        params.save_path = self._save_path
        self._lt_handle = self._lt_session.add_torrent(params)

        # Wait for metadata only (up to 60 s)
        deadline = time.monotonic() + 60
        while not self._lt_handle.has_metadata():
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "Could not fetch torrent metadata within 60 s. "
                    "Check your network and the magnet link."
                )
            time.sleep(0.5)

        ti = self._lt_handle.torrent_file()
        self._torrent_info = ti
        self._torrent_name = ti.name()

        fs = ti.files()
        for i in range(fs.num_files()):
            fp = fs.file_path(i).replace("\\", "/").strip("/")
            self._file_sizes[fp] = fs.file_size(i)
            self._insert_into_tree(fp)

        # Don't download any file data yet
        self._lt_handle.prioritize_files([0] * fs.num_files())

    # ------------------------------------------------------------------
    # Tree building
    # ------------------------------------------------------------------

    def _insert_into_tree(self, path: str):
        from .link_parser import is_browsable_archive

        parts = path.split("/")
        node = self._tree
        for part in parts[:-1]:
            if part not in node or not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]

        leaf = parts[-1]
        if is_browsable_archive(leaf):
            node[leaf] = "__archive__"
        else:
            node[leaf] = None

    # ------------------------------------------------------------------
    # BaseArchiveHandler interface
    # ------------------------------------------------------------------

    @property
    def tree(self):
        return self._tree

    def namelist(self):
        if self._names is None:
            self._names = sorted(self._file_sizes.keys())
        return self._names

    def read(self, name):
        """Download only the requested file's pieces and return its bytes."""
        try:
            import libtorrent as lt
        except ImportError:
            raise RuntimeError(
                "Reading torrent file contents requires the 'libtorrent' package. "
                "Install it with: pip install libtorrent"
            )

        name = name.strip("/").replace("\\", "/")
        self._ensure_lt_session()

        ti = self._lt_handle.torrent_file()
        fs = ti.files()

        file_idx = None
        for i in range(fs.num_files()):
            if fs.file_path(i).replace("\\", "/").strip("/") == name:
                file_idx = i
                break

        if file_idx is None:
            raise FileNotFoundError(f"Not in torrent: {name}")

        # Prioritise only this file for sequential download
        prios = [0] * fs.num_files()
        prios[file_idx] = 7
        self._lt_handle.prioritize_files(prios)
        self._lt_handle.set_flags(lt.torrent_flags.sequential_download)

        # Wait for the file to finish (up to 5 min)
        deadline = time.monotonic() + 300
        while True:
            progress = self._lt_handle.file_progress()
            if progress[file_idx] >= fs.file_size(file_idx):
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"Download timed out for: {name}")
            time.sleep(0.5)

        disk_path = os.path.join(self._save_path, fs.file_path(file_idx))
        with open(disk_path, "rb") as fh:
            return fh.read()

    def close(self):
        if self._lt_handle and self._lt_session:
            try:
                self._lt_session.remove_torrent(self._lt_handle)
            except Exception:
                pass
        self._lt_session = None
        self._lt_handle = None

        if self._save_path:
            import shutil

            shutil.rmtree(self._save_path, ignore_errors=True)
            self._save_path = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_lt_session(self):
        """Lazily create a libtorrent session for file reads."""
        if self._lt_session and self._lt_handle:
            return

        import libtorrent as lt

        self._save_path = self._save_path or tempfile.mkdtemp(
            prefix="zipbrowser_torrent_"
        )

        settings = {
            "enable_dht": True,
            "enable_lsd": True,
            "enable_natpmp": True,
            "enable_upnp": True,
        }
        self._lt_session = lt.session(settings)
        self._lt_session.add_dht_router("router.bittorrent.com", 6881)
        self._lt_session.add_dht_router("router.utorrent.com", 6881)
        self._lt_session.add_dht_router("dht.transmissionbt.com", 6881)

        # Load from .torrent file
        ti = lt.torrent_info(self._torrent_file_path)
        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = self._save_path
        self._lt_handle = self._lt_session.add_torrent(params)
        self._torrent_info = ti

        # Don't download anything yet
        fs = ti.files()
        self._lt_handle.prioritize_files([0] * fs.num_files())

    def get_file_size(self, path: str):
        """Return size in bytes for *path*, or ``None``."""
        return self._file_sizes.get(path.strip("/").replace("\\", "/"))
