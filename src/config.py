"""
Configuration management for the ZIP file viewer application.
"""
import os
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    """Application configuration"""
    zip_paths: list[str]
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False
    multiuser: bool = True
    # Legacy single-user mode support
    username: Optional[str] = None
    password: Optional[str] = None

    @property
    def secret_key(self):
        """Generate a secret key for Flask sessions"""
        # Use a more secure approach for secret key generation
        if hasattr(self, "_secret_key"):
            return self._secret_key

        # Generate a random secret key or use environment variable
        env_key = os.environ.get("ZIP_VIEWER_SECRET_KEY")
        if env_key:
            self._secret_key = env_key
        else:
            # Generate a random key (will be different each restart)
            # In production, you might want to persist this
            self._secret_key = secrets.token_hex(32)

        return self._secret_key

    @property
    def zip_path(self):
        """Backward compatibility property - returns first zip path"""
        return self.zip_paths[0] if self.zip_paths else None

    @classmethod
    def from_args(cls, args):
        """Create configuration from command line arguments"""
        # Check if legacy single-user mode is being used
        if (
            hasattr(args, "username")
            and hasattr(args, "password")
            and args.username
            and args.password
        ):
            return cls(
                username=args.username,
                password=args.password,
                zip_paths=args.zip_paths,
                host=args.host,
                port=args.port,
                debug=getattr(args, "debug", False),
                multiuser=False,  # Legacy single-user mode
            )
        else:
            return cls(
                zip_paths=args.zip_paths,
                host=args.host,
                port=args.port,
                debug=getattr(args, "debug", False),
                multiuser=True,
            )
