"""
Utility functions for the ZIP file viewer application.
"""
import os
import hashlib
import urllib.parse


def get_zip_file_hash(zip_path):
    """Generate a unique hash for a zip file based on its path and modification time"""
    # Check if it's a URL
    try:
        result = urllib.parse.urlparse(zip_path)
        is_url = all([result.scheme, result.netloc]) and result.scheme in [
            "http",
            "https",
        ]
    except Exception:
        is_url = False

    if is_url:
        # For URLs, just use the URL itself as the hash input
        hash_input = zip_path
    else:
        # For local files, use path, size, and modification time
        stat = os.stat(zip_path)
        hash_input = f"{zip_path}_{stat.st_size}_{stat.st_mtime}"

    return hashlib.md5(hash_input.encode()).hexdigest()[:12]


def is_image(filename):
    """Check if a file is an image based on its extension"""
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
    ext = os.path.splitext(filename.lower())[1]
    return ext in image_extensions


def is_system_file(filename):
    """Check if a file is a system/metadata file that should be filtered out"""
    filename_lower = filename.lower()
    # macOS metadata files
    if filename_lower.startswith("._") or "__macosx" in filename_lower:
        return True
    # Windows metadata files
    if filename_lower in ["thumbs.db", "desktop.ini"]:
        return True
    # Hidden files
    if filename_lower.startswith(".ds_store"):
        return True
    return False


def should_show_file(filename):
    """Check if a file should be shown in listings (filter out system files)"""
    return not is_system_file(filename)


def get_file_icon(extension):
    """Return appropriate icon class for file extension"""
    icons = {
        ".pdf": "icon-file-pdf",
        ".doc": "icon-file-word",
        ".docx": "icon-file-word",
        ".txt": "icon-file-text",
        ".md": "icon-file-text",
        ".zip": "icon-file-archive",
        ".rar": "icon-file-archive",
        ".7z": "icon-file-archive",
        ".mp3": "icon-file-audio",
        ".wav": "icon-file-audio",
        ".flac": "icon-file-audio",
        ".mp4": "icon-file-video",
        ".avi": "icon-file-video",
        ".mkv": "icon-file-video",
        ".py": "icon-file-code",
        ".js": "icon-file-code",
        ".html": "icon-file-code",
        ".css": "icon-file-code",
        ".json": "icon-file-code",
        ".exe": "icon-file-binary",
        ".msi": "icon-file-binary",
        ".iso": "icon-file-disk",
        ".img": "icon-file-disk",
    }
    return icons.get(extension.lower(), "icon-file")


def validate_pagination_params(page, per_page, thumb_size):
    """Validate and sanitize pagination parameters"""
    # Ensure page is valid
    try:
        page = max(1, int(page))
    except (ValueError, TypeError):
        page = 1
    
    # Ensure per_page is valid
    try:
        per_page = max(1, min(100, int(per_page)))  # Limit to 100 items per page
    except (ValueError, TypeError):
        per_page = 30
    
    # Ensure thumb_size is valid
    allowed_sizes = [80, 100, 150, 200, 250]
    try:
        thumb_size = int(thumb_size)
        if thumb_size not in allowed_sizes:
            thumb_size = 100
    except (ValueError, TypeError):
        thumb_size = 100
    
    return page, per_page, thumb_size
