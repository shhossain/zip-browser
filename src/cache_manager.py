"""
Cache manager for video and thumbnail caching with automatic cleanup.
Uses session-based encryption for one-time use files.
"""

import os
import shutil
import tempfile
import hashlib
import secrets
import threading
import time
import atexit
from cryptography.fernet import Fernet
from typing import Dict, Set, Optional


class CacheManager:
    """Manages video and thumbnail caching with encryption and automatic cleanup."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        
        # Generate runtime encryption key
        self._encryption_key = Fernet.generate_key()
        self._cipher = Fernet(self._encryption_key)
        
        # Create cache directories
        self._base_cache_dir = tempfile.mkdtemp(prefix="zip_browser_cache_")
        self._video_cache_dir = os.path.join(self._base_cache_dir, "videos")
        self._thumb_cache_dir = os.path.join(self._base_cache_dir, "thumbs")
        self._temp_dir = os.path.join(self._base_cache_dir, "temp")
        
        os.makedirs(self._video_cache_dir, exist_ok=True)
        os.makedirs(self._thumb_cache_dir, exist_ok=True)
        os.makedirs(self._temp_dir, exist_ok=True)
        
        # Track active sessions and their cache files
        self._active_sessions: Dict[str, Set[str]] = {}
        self._file_access_times: Dict[str, float] = {}
        self._session_lock = threading.Lock()
        
        # Maximum cache size (1GB default - more generous)
        self._max_cache_size = 1024 * 1024 * 1024
        
        # Cache cleanup interval (15 minutes - less aggressive)
        self._cleanup_interval = 900
        
        # Start cleanup thread
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        
        # Register cleanup on exit
        atexit.register(self.cleanup_all)
    
    def _generate_cache_key(self, zip_id: str, path: str) -> str:
        """Generate a unique cache key for a file."""
        data = f"{zip_id}_{path}_{secrets.token_hex(8)}".encode()
        return hashlib.sha256(data).hexdigest()[:32]
    
    def _get_stable_cache_key(self, zip_id: str, path: str) -> str:
        """Generate a stable cache key for a file (for checking existing cache)."""
        data = f"{zip_id}_{path}".encode()
        return hashlib.md5(data).hexdigest()
    
    def get_video_cache_path(self, zip_id: str, path: str) -> str:
        """Get the cache path for a transcoded video."""
        cache_key = self._get_stable_cache_key(zip_id, path)
        return os.path.join(self._video_cache_dir, f"{cache_key}.mp4")
    
    def get_thumb_cache_path(self, zip_id: str, path: str, thumb_type: str = "static") -> str:
        """Get the cache path for a video thumbnail."""
        cache_key = self._get_stable_cache_key(zip_id, path)
        ext = "gif" if thumb_type == "gif" else "jpg"
        return os.path.join(self._thumb_cache_dir, f"{cache_key}_{thumb_type}.{ext}")

    def get_sub_cache_dir(self, zip_id: str, path: str) -> str:
        """Get the cache directory for extracted subtitle VTT files."""
        cache_key = self._get_stable_cache_key(zip_id, path)
        return os.path.join(self._base_cache_dir, "subs", cache_key)

    def get_temp_path(self, suffix: str = "") -> str:
        """Get a temporary file path that will be auto-cleaned."""
        return os.path.join(self._temp_dir, f"{secrets.token_hex(16)}{suffix}")
    
    def cache_exists(self, cache_path: str) -> bool:
        """Check if a cached file exists."""
        return os.path.exists(cache_path)
    
    def register_session(self, session_id: str) -> None:
        """Register a new user session."""
        with self._session_lock:
            if session_id not in self._active_sessions:
                self._active_sessions[session_id] = set()
    
    def track_file_access(self, session_id: str, cache_path: str) -> None:
        """Track that a session is using a cached file."""
        with self._session_lock:
            if session_id not in self._active_sessions:
                self._active_sessions[session_id] = set()
            self._active_sessions[session_id].add(cache_path)
            self._file_access_times[cache_path] = time.time()
    
    def release_session_files(self, session_id: str) -> None:
        """Release all files associated with a session and clean up unused ones."""
        with self._session_lock:
            if session_id in self._active_sessions:
                files_to_check = self._active_sessions.pop(session_id, set())
                
                # Check if any other session is using these files
                for file_path in files_to_check:
                    still_in_use = any(
                        file_path in files 
                        for sid, files in self._active_sessions.items()
                        if sid != session_id
                    )
                    if not still_in_use:
                        self._delete_file_safe(file_path)
    
    def release_video(self, session_id: str, zip_id: str, path: str) -> None:
        """Release a specific video file when user exits video player."""
        cache_path = self.get_video_cache_path(zip_id, path)
        with self._session_lock:
            if session_id in self._active_sessions:
                self._active_sessions[session_id].discard(cache_path)
            
            # Check if any session is still using this file
            still_in_use = any(
                cache_path in files 
                for files in self._active_sessions.values()
            )
            if not still_in_use:
                self._delete_file_safe(cache_path)
    
    def release_folder_cache(self, session_id: str, zip_id: str, folder_path: str) -> None:
        """Release all cached files for a folder when user navigates away."""
        prefix = self._get_stable_cache_key(zip_id, folder_path)[:8]
        
        with self._session_lock:
            if session_id in self._active_sessions:
                files_to_remove = [
                    f for f in self._active_sessions[session_id]
                    if prefix in f
                ]
                for file_path in files_to_remove:
                    self._active_sessions[session_id].discard(file_path)
                    still_in_use = any(
                        file_path in files 
                        for sid, files in self._active_sessions.items()
                        if sid != session_id
                    )
                    if not still_in_use:
                        self._delete_file_safe(file_path)
    
    def _delete_file_safe(self, file_path: str) -> None:
        """Safely delete a file."""
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                self._file_access_times.pop(file_path, None)
        except Exception as e:
            print(f"Error deleting cache file {file_path}: {e}")
    
    def _get_cache_size(self) -> int:
        """Get total size of all cached files."""
        total_size = 0
        for directory in [self._video_cache_dir, self._thumb_cache_dir, self._temp_dir]:
            for root, _, files in os.walk(directory):
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        return total_size
    
    def _cleanup_old_files(self) -> None:
        """Clean up old cached files that haven't been accessed recently."""
        current_time = time.time()
        max_age = 7200  # 2 hours - keep cache longer
        
        with self._session_lock:
            # Get all files currently in use
            in_use_files = set()
            for files in self._active_sessions.values():
                in_use_files.update(files)
        
        # Clean temp directory (clean files older than 10 minutes)
        for f in os.listdir(self._temp_dir):
            file_path = os.path.join(self._temp_dir, f)
            try:
                if current_time - os.path.getmtime(file_path) > 600:
                    os.unlink(file_path)
            except Exception:
                pass
        
        # Clean video and thumb caches based on access time
        for directory in [self._video_cache_dir, self._thumb_cache_dir]:
            for f in os.listdir(directory):
                file_path = os.path.join(directory, f)
                
                # Skip files in use
                if file_path in in_use_files:
                    continue
                
                # Check last access time
                last_access = self._file_access_times.get(file_path, 0)
                if current_time - last_access > max_age:
                    self._delete_file_safe(file_path)
        
        # If still over max size, delete oldest files
        while self._get_cache_size() > self._max_cache_size:
            oldest_file = None
            oldest_time = float('inf')
            
            for directory in [self._video_cache_dir, self._thumb_cache_dir]:
                for f in os.listdir(directory):
                    file_path = os.path.join(directory, f)
                    if file_path in in_use_files:
                        continue
                    access_time = self._file_access_times.get(file_path, 0)
                    if access_time < oldest_time:
                        oldest_time = access_time
                        oldest_file = file_path
            
            if oldest_file:
                self._delete_file_safe(oldest_file)
            else:
                break
    
    def _cleanup_loop(self) -> None:
        """Background thread for periodic cache cleanup."""
        while True:
            time.sleep(self._cleanup_interval)
            try:
                self._cleanup_old_files()
            except Exception as e:
                print(f"Cache cleanup error: {e}")
    
    def cleanup_all(self) -> None:
        """Clean up all cache directories on shutdown."""
        try:
            if os.path.exists(self._base_cache_dir):
                shutil.rmtree(self._base_cache_dir)
        except Exception as e:
            print(f"Error cleaning up cache: {e}")


# Global cache manager instance
cache_manager = CacheManager()
