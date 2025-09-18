"""
ZIP file management functionality.
"""
import os
import glob
import pyzipper
import urllib.parse
from remotezip import RemoteZip
from .utils import get_zip_file_hash, is_image, should_show_file


class ZipManager:
    """Manages ZIP file operations and caching."""

    def __init__(self):
        self.zip_files = (
            {}
        )  # zip_id -> {"path": path, "password": password, "zfile": pyzipper.AESZipFile or RemoteZip, "tree": dict}

    def is_url(self, path):
        """Check if a path is a URL"""
        try:
            result = urllib.parse.urlparse(path)
            return all([result.scheme, result.netloc]) and result.scheme in [
                "http",
                "https",
            ]
        except Exception:
            return False

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
        """Check if a ZIP file requires a password"""
        try:
            if self.is_url(zip_path):
                # For remote ZIP files, use RemoteZip
                with RemoteZip(zip_path) as zf:
                    # Only check actual files, not directories
                    file_entries = [f for f in zf.namelist() if not f.endswith("/")]
                    if file_entries:
                        # Try to read the first file without password
                        try:
                            zf.read(file_entries[0])
                            return False  # No password required
                        except (RuntimeError, Exception):
                            return True  # Password required
                    return False  # Empty zip or only directories
            else:
                # For local ZIP files, use pyzipper
                with pyzipper.AESZipFile(zip_path, "r") as zf:
                    # Only check actual files, not directories
                    file_entries = [f for f in zf.namelist() if not f.endswith("/")]
                    if file_entries:
                        # Try to read the first file without password
                        try:
                            zf.read(file_entries[0])
                            return False  # No password required
                        except (
                            RuntimeError,
                            pyzipper.BadZipFile,
                            pyzipper.LargeZipFile,
                        ):
                            return True  # Password required
                    return False  # Empty zip or only directories
        except Exception:
            return False

    def validate_zip_password(self, zip_path, password):
        """Validate if the provided password works for the ZIP file"""
        try:
            if self.is_url(zip_path):
                # For remote ZIP files, use RemoteZip
                with RemoteZip(zip_path) as zf:
                    if password:
                        zf.setpassword(password.encode("utf-8"))
                    # Try to read the first file to validate password
                    if zf.namelist():
                        zf.read(zf.namelist()[0])
                    return True
            else:
                # For local ZIP files, use pyzipper
                with pyzipper.AESZipFile(zip_path, "r") as zf:
                    if password:
                        zf.setpassword(password.encode("utf-8"))
                    # Try to read the first file to validate password
                    if zf.namelist():
                        zf.read(zf.namelist()[0])
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
        """Discover ZIP files from the provided path"""
        zip_path = zip_path.replace("\\", "/").replace('"', "")

        # Check if it's a URL
        if self.is_url(zip_path):
            return [zip_path]

        # Check if it's a text file containing URLs
        if os.path.isfile(zip_path) and zip_path.lower().endswith(".txt"):
            urls = self.read_urls_from_file(zip_path)
            if urls:
                return urls
            # If no URLs found, treat as regular file (might be a zip file with .txt extension)

        if os.path.isfile(zip_path):
            # Single ZIP file
            return [zip_path]
        elif os.path.isdir(zip_path):
            # Directory - find all ZIP files
            zip_files_list = []
            for ext in ["*.zip", "*.iso"]:
                zip_files_list.extend(glob.glob(os.path.join(zip_path, ext)))
                zip_files_list.extend(
                    glob.glob(os.path.join(zip_path, "**", ext), recursive=True)
                )
            return zip_files_list
        else:
            return []

    def load_zip_file(self, zip_id, password=None):
        """Load and cache a zip file"""
        if zip_id not in self.zip_files:
            return None

        zip_info = self.zip_files[zip_id]

        try:
            if self.is_url(zip_info["path"]):
                # For remote ZIP files, use RemoteZip
                zfile = RemoteZip(zip_info["path"])
            else:
                # For local ZIP files, use pyzipper
                zfile = pyzipper.AESZipFile(zip_info["path"])

            if password:
                zfile.setpassword(password.encode("utf-8"))
                zip_info["password"] = password
            elif zip_info.get("password"):
                zfile.setpassword(zip_info["password"].encode("utf-8"))

            # Test the password by trying to read the first file
            if zfile.namelist():
                zfile.read(zfile.namelist()[0])

            zip_info["zfile"] = zfile
            zip_info["tree"] = self.build_zip_tree(zfile)
            return zfile
        except Exception:
            return None

    def get_zip_file_object(self, zip_id):
        """Get a fresh zip file object for reading files (especially important for remote ZIPs)"""
        if zip_id not in self.zip_files:
            return None

        zip_info = self.zip_files[zip_id]

        try:
            if self.is_url(zip_info["path"]):
                # For remote ZIP files, create a new RemoteZip object each time
                zfile = RemoteZip(zip_info["path"])
            else:
                # For local ZIP files, also create a new object each time to avoid "already closed" errors
                zfile = pyzipper.AESZipFile(zip_info["path"])

            # Apply password if needed
            if zip_info.get("password"):
                zfile.setpassword(zip_info["password"].encode("utf-8"))

            return zfile
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
            search_type: Type of search - "all", "images", "folders", "files"

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

                    # Filter based on search type
                    include = False
                    if search_type == "all":
                        include = True
                    elif search_type == "images" and is_image_file:
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
