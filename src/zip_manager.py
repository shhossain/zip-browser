"""
Archive file management functionality.
Supports ZIP, RAR, 7Z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, GZ, and remote ZIP URLs.
"""
import os
import glob
import urllib.parse

from .archive_handler import (
    open_archive,
    is_url as _is_url,
    ARCHIVE_GLOB_PATTERNS,
)
from .utils import get_zip_file_hash, is_image, is_video, should_show_file


class ZipManager:
    """Manages ZIP file operations and caching."""

    def __init__(self):
        self.zip_files = (
            {}
        )  # zip_id -> {"path": ..., "password": ..., "zfile": ArchiveFile, "tree": dict}

    def is_url(self, path):
        """Check if a path is a URL"""
        return _is_url(path)

    def read_urls_from_file(self, file_path):
        """Read URLs from a text file"""
        urls = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and self.is_url(line):
                        urls.append(line)
        except Exception:
            pass
        return urls

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
        """Build file tree structure from zip file"""
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
                    cur[parts[-1]] = None
        return zip_tree

    def discover_zip_files(self, zip_path):
        """Discover archive files from the provided path"""
        zip_path = zip_path.replace("\\", "/").replace('"', "")

        # Check if it's a URL
        if self.is_url(zip_path):
            return [zip_path]

        # Check if it's a text file containing URLs
        if os.path.isfile(zip_path) and zip_path.lower().endswith(".txt"):
            urls = self.read_urls_from_file(zip_path)
            if urls:
                return urls

        if os.path.isfile(zip_path):
            return [zip_path]
        elif os.path.isdir(zip_path):
            archive_files_list = []
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

            # Test by reading the first file entry
            file_entries = [f for f in zfile.namelist() if not f.endswith("/")]
            if file_entries:
                zfile.read(file_entries[0])

            zip_info["zfile"] = zfile
            zip_info["tree"] = self.build_zip_tree(zfile)
            return zfile
        except Exception:
            return None

    def get_zip_file_object(self, zip_id):
        """Get a fresh archive file object for reading files."""
        if zip_id not in self.zip_files:
            return None

        zip_info = self.zip_files[zip_id]

        try:
            pwd = zip_info["password"].encode("utf-8") if zip_info.get("password") else None
            return open_archive(zip_info["path"], password=pwd)
        except Exception:
            return None

    def initialize_zip_files(self, zip_paths):
        """Initialize available ZIP files from multiple paths"""
        # Handle both single path (for backward compatibility) and multiple paths
        if isinstance(zip_paths, str):
            zip_paths = [zip_paths]

        for zip_path in zip_paths:
            available_zips = self.discover_zip_files(zip_path)
            for zip_file_path in available_zips:
                zip_id = get_zip_file_hash(zip_file_path)

                # Generate appropriate name for the ZIP file
                if self.is_url(zip_file_path):
                    # For URLs, extract filename from the URL path
                    parsed_url = urllib.parse.urlparse(zip_file_path)
                    zip_name = (
                        os.path.basename(parsed_url.path)
                        or f"remote_zip_{hash(zip_file_path) % 10000}.zip"
                    )
                else:
                    # For local files, use the basename
                    zip_name = os.path.basename(zip_file_path)

                self.zip_files[zip_id] = {
                    "path": zip_file_path,
                    "name": zip_name,
                    "is_remote": self.is_url(zip_file_path),
                    "requires_password": self.check_zip_requires_password(
                        zip_file_path
                    ),
                    "password": None,
                    "zfile": None,
                    "tree": {},
                }

    def get_dir_tree(self, zip_id, path):
        """Get directory tree for a specific zip file and path"""
        if zip_id not in self.zip_files or not self.zip_files[zip_id]["tree"]:
            return None

        parts = path.strip("/").split("/") if path.strip("/") else []
        cur = self.zip_files[zip_id]["tree"]
        for p in parts:
            if p in cur:
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
                if tree[name] is None:  # It's a file
                    if is_image(name):
                        return full_path
                else:  # It's a folder
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

                # Check if the name matches the query
                if query_lower in name_lower:
                    is_folder = tree[name] is not None
                    is_image_file = not is_folder and is_image(name)
                    is_video_file = not is_folder and is_video(name)

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
                    elif search_type == "files" and not is_folder:
                        include = True

                    if include:
                        result = {
                            "name": name,
                            "path": full_path,
                            "is_folder": is_folder,
                            "is_image": is_image_file,
                            "is_video": is_video_file,
                            "directory": current_path or "/",
                            "extension": (
                                os.path.splitext(name.lower())[1]
                                if not is_folder
                                else ""
                            ),
                        }
                        results.append(result)

                # Recursively search in subdirectories
                if tree[name] is not None:  # It's a folder
                    search_in_tree(tree[name], full_path)

        search_in_tree(zip_tree)
        return results
