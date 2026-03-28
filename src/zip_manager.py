"""
Archive file management functionality.
Supports ZIP, RAR, 7Z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, GZ, remote ZIP URLs,
browsable web URLs, .torrent files, and magnet URLs.
Supports nested archives with optional password.
"""
import os
import glob
import hashlib
import tempfile
import urllib.parse
from .archive_handlers import (
    open_archive,
    is_url as _is_url,
    is_archive_url as _is_archive_url,
    is_magnet as _is_magnet,
    is_nested_archive,
    ARCHIVE_GLOB_PATTERNS,
)
from .utils import get_source_hash, is_image, is_video, should_show_file


class _ReadOnlyProxy:
    """Lightweight proxy that delegates read/namelist but ignores close.

    Used to hand out the long-lived URL handler to callers that expect to
    close the object after a single read (thumb / view_file routes).
    """

    def __init__(self, handler):
        self._h = handler

    def read(self, name):
        return self._h.read(name)

    def namelist(self):
        return self._h.namelist()

    def close(self):
        pass  # intentionally no-op

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class ZipManager:
    """Manages ZIP file operations and caching."""

    def __init__(self):
        self.zip_files = (
            {}
        )  # zip_id -> {"path": ..., "password": ..., "zfile": ArchiveFile, "tree": dict}

    def is_url(self, path):
        """Check if a path is a URL"""
        return _is_url(path)

    def is_magnet(self, path):
        """Check if a path is a magnet URL"""
        return _is_magnet(path)

    def read_urls_from_file(self, file_path):
        """Read URLs from a text file"""
        urls = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and (self.is_url(line) or self.is_magnet(line)):
                        urls.append(line)
        except Exception:
            pass
        return urls

    @staticmethod
    def read_url_shortcut(file_path):
        """Read a URL from a ``.url`` shortcut file (INI-style).

        Returns the URL string, or ``None`` on failure.
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.upper().startswith("URL="):
                        url = line[4:].strip()
                        if url:
                            return url
        except Exception:
            pass
        return None

    def check_zip_requires_password(self, zip_path):
        """Check if an archive file requires a password"""
        try:
            with open_archive(zip_path) as zf:
                file_entries = [f for f in zf.namelist() if not f.endswith("/")]
                if file_entries:
                    try:
                        zf.read(file_entries[0])
                        return False
                    except Exception:
                        return True
                return False
        except Exception:
            return False

    def validate_zip_password(self, zip_path, password):
        """Validate if the provided password works for the archive file"""
        try:
            pwd = password.encode("utf-8") if password else None
            with open_archive(zip_path, password=pwd) as zf:
                file_entries = [f for f in zf.namelist() if not f.endswith("/")]
                if file_entries:
                    zf.read(file_entries[0])
                return True
        except Exception:
            return False

    def build_zip_tree(self, zfile):
        """Build file tree structure from zip file.

        Files that are themselves supported archives are stored with the
        sentinel value ``"__archive__"`` instead of ``None`` so that the
        UI can render them as browsable entries.

        Handlers that maintain their own tree (e.g. URL handler) bypass
        the generic builder.
        """
        # Use the handler's own tree when available (e.g. UrlHandler)
        if hasattr(zfile, 'tree') and zfile.tree is not None:
            return zfile.tree

        zip_tree = {}
        for name in zfile.namelist():
            # Skip system files and metadata files
            if not should_show_file(name):
                continue

            parts = name.strip("/").split("/")
            cur = zip_tree
            for part in parts[:-1]:
                # Also check if directory parts should be shown
                if should_show_file(part):
                    cur = cur.setdefault(part, {})
                else:
                    # Skip the entire path if any part is a system file
                    break
            else:
                # Only add file if we didn't break out of the loop
                if not name.endswith("/"):
                    if is_nested_archive(name):
                        cur[parts[-1]] = "__archive__"
                    else:
                        cur[parts[-1]] = None
        return zip_tree

    def discover_zip_files(self, zip_path):
        """Discover archive files from the provided path"""
        zip_path = zip_path.replace("\\", "/").replace('"', "")

        # Check if it's a URL
        if self.is_url(zip_path):
            return [zip_path]

        # Check if it's a magnet URL
        if self.is_magnet(zip_path):
            return [zip_path]

        # Expand ~ for local paths
        zip_path = os.path.expanduser(zip_path)
        
        # Check if exists
        if not os.path.exists(zip_path):
            print("File does not exists")
            return []

        # Check if it's a .url shortcut file
        if os.path.isfile(zip_path) and zip_path.lower().endswith('.url'):
            url = self.read_url_shortcut(zip_path)
            if url:
                return [url]
            return []

        # Check if it's a text file containing URLs
        if os.path.isfile(zip_path) and zip_path.lower().endswith(".txt"):
            urls = self.read_urls_from_file(zip_path)
            if urls:
                return urls

        if os.path.isfile(zip_path):
            return [zip_path]
        
        elif os.path.isdir(zip_path):
            archive_files_list = []
            # Include the directory for filesystem browsing if readable
            try:
                os.listdir(zip_path)
                archive_files_list.append(zip_path)
            except OSError:
                print(
                    f"Warning: Cannot list directory '{zip_path}' (permission denied). "
                    f"On macOS, grant Full Disk Access to your terminal app "
                    f"in System Settings > Privacy & Security > Full Disk Access."
                )
            seen = set()
            for pattern in ARCHIVE_GLOB_PATTERNS:
                for match in glob.glob(os.path.join(zip_path, pattern)):
                    real = os.path.realpath(match)
                    if real not in seen:
                        seen.add(real)
                        archive_files_list.append(match)
                for match in glob.glob(
                    os.path.join(zip_path, "**", pattern), recursive=True
                ):
                    real = os.path.realpath(match)
                    if real not in seen:
                        seen.add(real)
                        archive_files_list.append(match)

            # Also discover .url shortcut files
            for match in glob.glob(os.path.join(zip_path, "*.url")):
                url = self.read_url_shortcut(match)
                if url and url not in seen:
                    seen.add(url)
                    archive_files_list.append(url)
            for match in glob.glob(
                os.path.join(zip_path, "**", "*.url"), recursive=True
            ):
                url = self.read_url_shortcut(match)
                if url and url not in seen:
                    seen.add(url)
                    archive_files_list.append(url)

            return archive_files_list
        else:
            return []

    def load_zip_file(self, zip_id, password=None):
        """Load and cache an archive file"""
        if zip_id not in self.zip_files:
            return None

        zip_info = self.zip_files[zip_id]

        try:
            pwd = None
            if password:
                pwd = password.encode("utf-8")
                zip_info["password"] = password
            elif zip_info.get("password"):
                pwd = zip_info["password"].encode("utf-8")

            zfile = open_archive(zip_info["path"], password=pwd)

            # Test by reading the first file entry — skip for handlers
            # that manage their own tree (URL handlers already verified
            # connectivity during __init__).
            if not hasattr(zfile, 'tree'):
                file_entries = [f for f in zfile.namelist() if not f.endswith("/")]
                if file_entries:
                    zfile.read(file_entries[0])

            tree = self.build_zip_tree(zfile)
            # Set both atomically so we never end up with zfile set
            # but tree empty (which causes 404 on subsequent attempts).
            zip_info["zfile"] = zfile
            zip_info["tree"] = tree
            return zfile
        except Exception:
            return None

    def get_zip_file_object(self, zip_id):
        """Get a fresh archive file object for reading files.

        For handlers that maintain their own state (URL handlers), a
        lightweight proxy is returned so that the caller's ``close()``
        does not tear down the long-lived session.
        """
        if zip_id not in self.zip_files:
            return None

        zip_info = self.zip_files[zip_id]

        # Reuse URL handlers via a non-closable proxy
        existing = zip_info.get("zfile")
        if existing and hasattr(existing, 'tree'):
            return _ReadOnlyProxy(existing)

        try:
            pwd = zip_info["password"].encode("utf-8") if zip_info.get("password") else None
            return open_archive(zip_info["path"], password=pwd)
        except Exception:
            return None

    def get_file_url(self, zip_id, path):
        """Return a direct URL for a file if the handler supports it.

        For ``UrlHandler`` entries this returns the remote URL so callers
        (e.g. ffmpeg, the view route) can stream from it directly without
        downloading through ``read()``.  Returns ``None`` otherwise.
        """
        if zip_id not in self.zip_files:
            return None
        zfile = self.zip_files[zip_id].get("zfile")
        if zfile and hasattr(zfile, 'get_url'):
            return zfile.get_url(path)
        return None

    # ------------------------------------------------------------------
    # Nested archive support
    # ------------------------------------------------------------------

    @staticmethod
    def get_nested_archive_id(parent_zip_id, inner_path):
        """Generate a deterministic ID for a nested archive."""
        return hashlib.md5(f"{parent_zip_id}:{inner_path}".encode()).hexdigest()[:12]

    def is_item_archive(self, zip_id, path):
        """Check if an item in the tree is a nested archive (``__archive__`` sentinel)."""
        if zip_id not in self.zip_files or not self.zip_files[zip_id]["tree"]:
            return False
        parts = path.strip("/").split("/") if path.strip("/") else []
        cur = self.zip_files[zip_id]["tree"]
        for p in parts[:-1]:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return False
        if parts:
            return cur.get(parts[-1]) == "__archive__"
        return False

    def open_nested_archive(self, parent_zip_id, inner_path, password=None):
        """Extract a nested archive from its parent and register it for browsing.

        Returns ``(nested_id, needs_password)`` on success or ``(None, False)``
        on unrecoverable failure.
        """
        nested_id = self.get_nested_archive_id(parent_zip_id, inner_path)

        # Already opened?
        if nested_id in self.zip_files and self.zip_files[nested_id].get("zfile"):
            return nested_id, False

        parent_info = self.zip_files.get(parent_zip_id)
        if not parent_info:
            return None, False

        # Extract inner archive bytes from parent
        try:
            parent_zfile = self.get_zip_file_object(parent_zip_id)
            if not parent_zfile:
                return None, False
            inner_bytes = parent_zfile.read(inner_path)
            parent_zfile.close()
        except Exception:
            return None, False

        # Write to a temp file so ArchiveFile can open it
        inner_name = os.path.basename(inner_path)
        tmp_dir = tempfile.mkdtemp(prefix="zipbrowser_nested_")
        tmp_path = os.path.join(tmp_dir, inner_name)
        with open(tmp_path, "wb") as f:
            f.write(inner_bytes)

        # Register the nested archive entry (even before unlocking)
        if nested_id not in self.zip_files:
            self.zip_files[nested_id] = {
                "path": tmp_path,
                "name": inner_name,
                "is_remote": False,
                "requires_password": False,
                "password": None,
                "zfile": None,
                "tree": {},
                "nested": True,
                "parent_zip_id": parent_zip_id,
                "inner_path": inner_path,
                "_tmp_dir": tmp_dir,
            }

        # Check if password is needed
        needs_password = self.check_zip_requires_password(tmp_path)
        self.zip_files[nested_id]["requires_password"] = needs_password

        if needs_password and not password:
            return nested_id, True

        # Try to load
        if password:
            self.zip_files[nested_id]["password"] = password
        result = self.load_zip_file(nested_id, password=password)
        if result is None and not needs_password:
            return None, False

        return nested_id, False

    def cleanup_nested_archives(self):
        """Remove temp files for all nested archives."""
        import shutil
        for zip_id, info in list(self.zip_files.items()):
            if info.get("nested") and info.get("_tmp_dir"):
                shutil.rmtree(info["_tmp_dir"], ignore_errors=True)
                del self.zip_files[zip_id]

    def initialize_zip_files(self, zip_paths):
        """Initialize available ZIP files from multiple paths"""
        # Handle both single path (for backward compatibility) and multiple paths
        if isinstance(zip_paths, str):
            zip_paths = [zip_paths]

        for zip_path in zip_paths:
            available_zips = self.discover_zip_files(zip_path)
            for zip_file_path in available_zips:
                zip_id = get_source_hash(zip_file_path)

                # Generate appropriate name for the ZIP file
                if self.is_magnet(zip_file_path):
                    # Extract display name from magnet link (dn= parameter)
                    try:
                        parsed = urllib.parse.urlparse(zip_file_path)
                        params = urllib.parse.parse_qs(parsed.query)
                        dn = params.get('dn', [None])[0]
                        zip_name = urllib.parse.unquote(dn) if dn else f"magnet_{hash(zip_file_path) % 10000}"
                    except Exception:
                        zip_name = f"magnet_{hash(zip_file_path) % 10000}"
                elif self.is_url(zip_file_path):
                    parsed_url = urllib.parse.urlparse(zip_file_path)
                    path_part = parsed_url.path.rstrip('/')
                    zip_name = os.path.basename(path_part) if path_part else ''
                    if not zip_name:
                        zip_name = parsed_url.netloc or f"remote_{hash(zip_file_path) % 10000}"
                    zip_name = urllib.parse.unquote(zip_name)
                else:
                    # For local files, use the basename
                    zip_name = os.path.basename(zip_file_path)

                # Skip expensive password check for non-archive web URLs,
                # magnets, and .torrent files (read() needs libtorrent).
                is_magnet = self.is_magnet(zip_file_path)
                is_web_url = self.is_url(zip_file_path) and not _is_archive_url(zip_file_path)
                is_torrent = zip_file_path.lower().endswith('.torrent')
                requires_pw = False if (is_web_url or is_magnet or is_torrent) else self.check_zip_requires_password(
                    zip_file_path
                )

                self.zip_files[zip_id] = {
                    "path": zip_file_path,
                    "name": zip_name,
                    "is_remote": self.is_url(zip_file_path) or is_magnet,
                    "requires_password": requires_pw,
                    "password": None,
                    "zfile": None,
                    "tree": {},
                }

    def get_dir_tree(self, zip_id, path):
        """Get directory tree for a specific zip file and path.

        Returns ``None`` for invalid paths.  If the path resolves to the
        ``"__archive__"`` sentinel the caller should handle it as a
        nested archive rather than a directory.

        For handlers that support lazy discovery (URL handlers), the
        page at *path* is crawled before the tree is read.
        """
        if zip_id not in self.zip_files:
            return None

        zip_info = self.zip_files[zip_id]

        # Trigger lazy discovery for handlers that support it
        zfile = zip_info.get("zfile")
        if zfile and hasattr(zfile, 'discover'):
            zfile.discover(path)
            # Sync the tree reference (discover mutates the handler's tree in-place)
            zip_info["tree"] = zfile.tree

        if not zip_info["tree"]:
            return None

        parts = path.strip("/").split("/") if path.strip("/") else []
        cur = self.zip_files[zip_id]["tree"]
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None
        return cur

    def get_first_image_in_folder(self, zip_id, folder_path):
        """Get the first image file in a folder (recursively)"""
        folder_tree = self.get_dir_tree(zip_id, folder_path)
        if folder_tree is None:
            return None

        def find_first_image(tree, current_path=""):
            for name in sorted(tree.keys()):
                full_path = current_path + "/" + name if current_path else name
                if tree[name] is None or tree[name] == "__archive__":  # file or nested archive
                    if is_image(name):
                        return full_path
                elif isinstance(tree[name], dict):  # folder
                    result = find_first_image(tree[name], full_path)
                    if result:
                        return result
            return None

        first_image = find_first_image(folder_tree)
        if first_image and folder_path:
            return folder_path + "/" + first_image
        return first_image

    def get_zip_info(self, zip_id):
        """Get zip file information"""
        return self.zip_files.get(zip_id)

    def get_all_zip_files(self):
        """Get all zip files"""
        return self.zip_files

    def search_files(self, zip_id, query, search_type="all"):
        """
        Search for files in a ZIP file.

        Args:
            zip_id: The ZIP file ID
            query: Search query string
            search_type: Type of search - "all", "images", "videos", "folders", "files"

        Returns:
            List of matching file paths with metadata
        """
        if zip_id not in self.zip_files or not self.zip_files[zip_id]["tree"]:
            return []

        query_lower = query.lower().strip()
        if not query_lower:
            return []

        results = []
        zip_tree = self.zip_files[zip_id]["tree"]

        def search_in_tree(tree, current_path=""):
            for name in tree.keys():
                name_lower = name.lower()
                full_path = current_path + "/" + name if current_path else name

                is_archive_entry = tree[name] == "__archive__"
                is_folder = isinstance(tree[name], dict)
                is_file = tree[name] is None or is_archive_entry

                # Check if the name matches the query
                if query_lower in name_lower:
                    is_image_file = is_file and is_image(name)
                    is_video_file = is_file and is_video(name)

                    # Filter based on search type
                    include = False
                    if search_type == "all":
                        include = True
                    elif search_type == "images" and is_image_file:
                        include = True
                    elif search_type == "videos" and is_video_file:
                        include = True
                    elif search_type == "folders" and is_folder:
                        include = True
                    elif search_type == "files" and is_file:
                        include = True

                    if include:
                        result = {
                            "name": name,
                            "path": full_path,
                            "is_folder": is_folder,
                            "is_image": is_image_file,
                            "is_video": is_video_file,
                            "is_archive": is_archive_entry,
                            "directory": current_path or "/",
                            "extension": (
                                os.path.splitext(name.lower())[1]
                                if not is_folder
                                else ""
                            ),
                        }
                        results.append(result)

                # Recursively search in subdirectories
                if is_folder:
                    search_in_tree(tree[name], full_path)

        search_in_tree(zip_tree)
        return results
