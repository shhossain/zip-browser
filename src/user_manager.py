"""
User management system for the ZIP file viewer application.
Handles user CRUD operations with JSON file storage and password hashing.
"""
import os
import json
import hashlib
import secrets
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class UserManager:
    """Manages user accounts with JSON file storage"""

    def __init__(self, app_name: str = "zip-browser"):
        self.app_name = app_name
        self.users_file = self._get_users_file_path()
        self._ensure_users_file_exists()

    def _get_users_file_path(self) -> Path:
        """Get the path to the users JSON file in user's home directory"""
        home_dir = Path.home()
        app_dir = home_dir / f".{self.app_name}"
        app_dir.mkdir(exist_ok=True)
        return app_dir / "users.json"

    def _ensure_users_file_exists(self):
        """Ensure the users file exists with proper structure"""
        if not self.users_file.exists():
            self._save_users({})

    def _load_users(self) -> Dict:
        """Load users from JSON file"""
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_users(self, users: Dict):
        """Save users to JSON file"""
        with open(self.users_file, 'w', encoding='utf-8') as f:
            json.dump(users, f, indent=2, ensure_ascii=False)

    def _hash_password(self, password: str, salt: str = None) -> Tuple[str, str]:
        """Hash password with salt using PBKDF2"""
        if salt is None:
            salt = secrets.token_hex(16)

        # Use PBKDF2 with SHA256 for secure password hashing
        hashed = hashlib.pbkdf2_hmac('sha256', 
                                   password.encode('utf-8'), 
                                   salt.encode('utf-8'), 
                                   100000)  # 100,000 iterations
        return hashed.hex(), salt

    def _verify_password(self, password: str, hashed_password: str, salt: str) -> bool:
        """Verify password against stored hash"""
        computed_hash, _ = self._hash_password(password, salt)
        return secrets.compare_digest(computed_hash, hashed_password)

    def create_user(self, username: str, password: str, email: str = None, 
                   is_admin: bool = False) -> bool:
        """Create a new user"""
        if not username or not password:
            raise ValueError("Username and password are required")

        users = self._load_users()

        # Check if user already exists
        if username in users:
            return False

        # Hash password
        hashed_password, salt = self._hash_password(password)

        # Create user record
        users[username] = {
            "username": username,
            "password_hash": hashed_password,
            "salt": salt,
            "email": email or "",
            "is_admin": is_admin,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "last_login": None,
            "active": True
        }

        self._save_users(users)
        return True

    def get_user(self, username: str) -> Optional[Dict]:
        """Get user information (without password hash)"""
        users = self._load_users()
        user = users.get(username)

        if user:
            # Return user info without sensitive data
            user_info = user.copy()
            user_info.pop('password_hash', None)
            user_info.pop('salt', None)
            return user_info

        return None

    def list_users(self) -> List[Dict]:
        """List all users (without password hashes)"""
        users = self._load_users()
        user_list = []

        for username, user_data in users.items():
            user_info = user_data.copy()
            user_info.pop('password_hash', None)
            user_info.pop('salt', None)
            user_list.append(user_info)

        return sorted(user_list, key=lambda x: x['username'])

    def update_user(self, username: str, **kwargs) -> bool:
        """Update user information"""
        users = self._load_users()

        if username not in users:
            return False

        user = users[username]

        # Update allowed fields
        allowed_fields = ['email', 'is_admin', 'active']
        for field, value in kwargs.items():
            if field in allowed_fields:
                user[field] = value

        # Handle password update separately
        if 'password' in kwargs:
            hashed_password, salt = self._hash_password(kwargs['password'])
            user['password_hash'] = hashed_password
            user['salt'] = salt

        user['updated_at'] = datetime.now().isoformat()

        self._save_users(users)
        return True

    def delete_user(self, username: str) -> bool:
        """Delete a user"""
        users = self._load_users()

        if username not in users:
            return False

        del users[username]
        self._save_users(users)
        return True

    def validate_credentials(self, username: str, password: str) -> bool:
        """Validate user credentials"""
        users = self._load_users()
        user = users.get(username)

        if not user or not user.get('active', True):
            return False

        return self._verify_password(password, user['password_hash'], user['salt'])

    def update_last_login(self, username: str):
        """Update user's last login timestamp"""
        users = self._load_users()
        if username in users:
            users[username]['last_login'] = datetime.now().isoformat()
            self._save_users(users)

    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """Change user password after validating old password"""
        if not self.validate_credentials(username, old_password):
            return False

        return self.update_user(username, password=new_password)

    def user_exists(self, username: str) -> bool:
        """Check if user exists"""
        users = self._load_users()
        return username in users

    def get_user_count(self) -> int:
        """Get total number of users"""
        users = self._load_users()
        return len(users)

    def is_admin(self, username: str) -> bool:
        """Check if user is an admin"""
        users = self._load_users()
        user = users.get(username)
        return user.get('is_admin', False) if user else False

    def get_users_file_location(self) -> str:
        """Get the location of the users file"""
        return str(self.users_file)
