"""
Unit tests for src/user_manager.py
"""
import json
import pytest

from src.user_manager import UserManager


class TestUserManagerCRUD:
    """Test user creation, read, update, delete operations."""

    def test_create_user(self, user_manager):
        assert user_manager.create_user("alice", "password123") is True
        assert user_manager.user_exists("alice") is True

    def test_create_duplicate_user(self, user_manager):
        user_manager.create_user("alice", "password123")
        assert user_manager.create_user("alice", "other") is False

    def test_create_user_empty_fields(self, user_manager):
        with pytest.raises(ValueError):
            user_manager.create_user("", "password123")
        with pytest.raises(ValueError):
            user_manager.create_user("bob", "")

    def test_get_user(self, user_manager):
        user_manager.create_user("alice", "pw", email="alice@test.com", is_admin=True)
        info = user_manager.get_user("alice")
        assert info is not None
        assert info["username"] == "alice"
        assert info["email"] == "alice@test.com"
        assert info["is_admin"] is True
        # Sensitive fields must be stripped
        assert "password_hash" not in info
        assert "salt" not in info

    def test_get_nonexistent_user(self, user_manager):
        assert user_manager.get_user("nobody") is None

    def test_list_users(self, user_manager):
        user_manager.create_user("charlie", "pw1")
        user_manager.create_user("alice", "pw2")
        users = user_manager.list_users()
        assert len(users) == 2
        # Sorted alphabetically
        assert users[0]["username"] == "alice"
        assert users[1]["username"] == "charlie"
        # No sensitive data
        for u in users:
            assert "password_hash" not in u

    def test_update_user_fields(self, user_manager):
        user_manager.create_user("alice", "pw")
        assert user_manager.update_user("alice", email="new@test.com", is_admin=True) is True
        info = user_manager.get_user("alice")
        assert info["email"] == "new@test.com"
        assert info["is_admin"] is True

    def test_update_nonexistent_user(self, user_manager):
        assert user_manager.update_user("nobody", email="x") is False

    def test_delete_user(self, user_manager):
        user_manager.create_user("alice", "pw")
        assert user_manager.delete_user("alice") is True
        assert user_manager.user_exists("alice") is False

    def test_delete_nonexistent_user(self, user_manager):
        assert user_manager.delete_user("nobody") is False

    def test_user_count(self, user_manager):
        assert user_manager.get_user_count() == 0
        user_manager.create_user("a", "pw")
        user_manager.create_user("b", "pw")
        assert user_manager.get_user_count() == 2


class TestUserManagerAuth:
    """Test authentication-related operations."""

    def test_validate_credentials_correct(self, user_manager):
        user_manager.create_user("alice", "correcthorse")
        assert user_manager.validate_credentials("alice", "correcthorse") is True

    def test_validate_credentials_wrong_password(self, user_manager):
        user_manager.create_user("alice", "correcthorse")
        assert user_manager.validate_credentials("alice", "wrongpassword") is False

    def test_validate_credentials_nonexistent_user(self, user_manager):
        assert user_manager.validate_credentials("ghost", "pw") is False

    def test_validate_credentials_inactive_user(self, user_manager):
        user_manager.create_user("alice", "pw")
        user_manager.update_user("alice", active=False)
        assert user_manager.validate_credentials("alice", "pw") is False

    def test_change_password(self, user_manager):
        user_manager.create_user("alice", "oldpw")
        assert user_manager.change_password("alice", "oldpw", "newpw") is True
        assert user_manager.validate_credentials("alice", "newpw") is True
        assert user_manager.validate_credentials("alice", "oldpw") is False

    def test_change_password_wrong_old(self, user_manager):
        user_manager.create_user("alice", "oldpw")
        assert user_manager.change_password("alice", "wrong", "newpw") is False

    def test_update_last_login(self, user_manager):
        user_manager.create_user("alice", "pw")
        user_manager.update_last_login("alice")
        # Verify by loading raw data
        users = user_manager._load_users()
        assert users["alice"]["last_login"] is not None

    def test_is_admin(self, user_manager):
        user_manager.create_user("admin", "pw", is_admin=True)
        user_manager.create_user("user", "pw", is_admin=False)
        assert user_manager.is_admin("admin") is True
        assert user_manager.is_admin("user") is False
        assert user_manager.is_admin("nobody") is False
