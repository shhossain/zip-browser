"""
Unit tests for src/auth.py
"""
from src.auth import User, AuthManager


class TestUser:
    def test_user_properties(self):
        user = User("alice", email="alice@test.com", is_admin=True)
        assert user.id == "alice"
        assert user.username == "alice"
        assert user.email == "alice@test.com"
        assert user.is_admin is True
        # Flask-Login mixin
        assert user.is_authenticated is True
        assert user.is_active is True

    def test_user_defaults(self):
        user = User("bob")
        assert user.email == ""
        assert user.is_admin is False


class TestAuthManager:
    def test_validate_credentials_success(self, user_manager):
        user_manager.create_user("alice", "pw123")
        am = AuthManager(user_manager)
        assert am.validate_credentials("alice", "pw123") is True

    def test_validate_credentials_failure(self, user_manager):
        user_manager.create_user("alice", "pw123")
        am = AuthManager(user_manager)
        assert am.validate_credentials("alice", "wrong") is False

    def test_load_user(self, user_manager):
        user_manager.create_user("alice", "pw123", email="a@b.com", is_admin=True)
        am = AuthManager(user_manager)
        user = am.load_user("alice")
        assert user is not None
        assert user.username == "alice"
        assert user.is_admin is True

    def test_load_user_nonexistent(self, user_manager):
        am = AuthManager(user_manager)
        assert am.load_user("nobody") is None

    def test_load_user_inactive(self, user_manager):
        user_manager.create_user("alice", "pw123")
        user_manager.update_user("alice", active=False)
        am = AuthManager(user_manager)
        assert am.load_user("alice") is None

    def test_is_admin(self, user_manager):
        user_manager.create_user("admin", "pw", is_admin=True)
        am = AuthManager(user_manager)
        assert am.is_admin("admin") is True
