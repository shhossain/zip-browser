"""
Tests for the CLI layer: UserCLI commands, create_main_parser, manage_users entry point.
"""
import argparse
import sys
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from src.user_cli import UserCLI, create_user_subparser
from src.user_manager import UserManager
from src.app import create_main_parser


# ---------------------------------------------------------------------------
# Helper: build a UserCLI backed by the test UserManager
# ---------------------------------------------------------------------------

@pytest.fixture()
def cli(user_manager):
    """UserCLI wired to the isolated test UserManager."""
    ucli = UserCLI.__new__(UserCLI)
    ucli.user_manager = user_manager
    return ucli


# ---------------------------------------------------------------------------
# create_main_parser
# ---------------------------------------------------------------------------

class TestCreateMainParser:
    def test_server_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["server", "/tmp/a.zip"])
        assert args.command == "server"
        assert args.zip_paths == ["/tmp/a.zip"]
        assert args.host == "0.0.0.0"
        assert args.port == 5000
        assert args.debug is False

    def test_server_with_options(self):
        parser = create_main_parser()
        args = parser.parse_args(["server", "/tmp/a.zip", "-H", "127.0.0.1",
                                  "-P", "8080", "-D"])
        assert args.host == "127.0.0.1"
        assert args.port == 8080
        assert args.debug is True

    def test_server_multiple_zip_paths(self):
        parser = create_main_parser()
        args = parser.parse_args(["server", "/a.zip", "/b.zip", "/c.zip"])
        assert args.zip_paths == ["/a.zip", "/b.zip", "/c.zip"]

    def test_user_create_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["user", "create", "alice", "-p", "pass", "-a"])
        assert args.command == "user"
        assert args.user_action == "create"
        assert args.username == "alice"
        assert args.password == "pass"
        assert args.admin is True

    def test_user_list_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["user", "list", "--detailed"])
        assert args.command == "user"
        assert args.user_action == "list"
        assert args.detailed is True

    def test_user_delete_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["user", "delete", "bob", "-f"])
        assert args.user_action == "delete"
        assert args.username == "bob"
        assert args.force is True

    def test_user_passwd_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["user", "passwd", "alice",
                                  "--old-password", "old", "--new-password", "new"])
        assert args.user_action == "passwd"
        assert args.old_password == "old"
        assert args.new_password == "new"

    def test_user_show_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["user", "show", "alice"])
        assert args.user_action == "show"
        assert args.username == "alice"

    def test_user_update_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["user", "update", "alice", "-e", "a@b.com", "-a"])
        assert args.user_action == "update"
        assert args.email == "a@b.com"
        assert args.admin is True

    def test_user_info_subcommand(self):
        parser = create_main_parser()
        args = parser.parse_args(["user", "info"])
        assert args.user_action == "info"


# ---------------------------------------------------------------------------
# UserCLI._create_user
# ---------------------------------------------------------------------------

class TestCLICreateUser:
    def test_create_with_password_flag(self, cli, capsys):
        args = SimpleNamespace(username="alice", password="pass123",
                               email="a@b.com", admin=False)
        assert cli._create_user(args) is True
        out = capsys.readouterr().out
        assert "created successfully" in out

    def test_create_admin(self, cli, capsys):
        args = SimpleNamespace(username="root", password="pw", email="", admin=True)
        assert cli._create_user(args) is True
        out = capsys.readouterr().out
        assert "administrator" in out

    def test_create_duplicate(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        args = SimpleNamespace(username="alice", password="pw", email="", admin=False)
        assert cli._create_user(args) is False
        assert "already exists" in capsys.readouterr().out

    def test_create_prompts_password(self, cli, capsys):
        """When no password flag, getpass is called."""
        with patch("src.user_cli.getpass.getpass", side_effect=["secret", "secret"]):
            args = SimpleNamespace(username="bob", password=None, email="", admin=False)
            assert cli._create_user(args) is True
        assert "created successfully" in capsys.readouterr().out

    def test_create_password_mismatch(self, cli, capsys):
        with patch("src.user_cli.getpass.getpass", side_effect=["aaa", "bbb"]):
            args = SimpleNamespace(username="bob", password=None, email="", admin=False)
            assert cli._create_user(args) is False
        assert "do not match" in capsys.readouterr().out

    def test_create_empty_password_prompt(self, cli, capsys):
        with patch("src.user_cli.getpass.getpass", side_effect=["", ""]):
            args = SimpleNamespace(username="bob", password=None, email="", admin=False)
            assert cli._create_user(args) is False
        assert "cannot be empty" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# UserCLI._list_users
# ---------------------------------------------------------------------------

class TestCLIListUsers:
    def test_list_empty(self, cli, capsys):
        args = SimpleNamespace(detailed=False)
        assert cli._list_users(args) is True
        assert "No users found" in capsys.readouterr().out

    def test_list_simple(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw", is_admin=True)
        user_manager.create_user("bob", "pw")
        args = SimpleNamespace(detailed=False)
        assert cli._list_users(args) is True
        out = capsys.readouterr().out
        assert "alice" in out
        assert "bob" in out
        assert "Total users: 2" in out

    def test_list_detailed(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw", email="a@b.com")
        args = SimpleNamespace(detailed=True)
        assert cli._list_users(args) is True
        out = capsys.readouterr().out
        assert "a@b.com" in out


# ---------------------------------------------------------------------------
# UserCLI._show_user
# ---------------------------------------------------------------------------

class TestCLIShowUser:
    def test_show_existing(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw", email="a@b.com", is_admin=True)
        args = SimpleNamespace(username="alice")
        assert cli._show_user(args) is True
        out = capsys.readouterr().out
        assert "alice" in out
        assert "a@b.com" in out
        assert "Yes" in out  # is_admin

    def test_show_nonexistent(self, cli, capsys):
        args = SimpleNamespace(username="nobody")
        assert cli._show_user(args) is False
        assert "not found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# UserCLI._update_user
# ---------------------------------------------------------------------------

class TestCLIUpdateUser:
    def test_update_email(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        args = SimpleNamespace(username="alice", email="new@x.com",
                               admin=False, no_admin=False, active=False, inactive=False)
        assert cli._update_user(args) is True
        assert "updated successfully" in capsys.readouterr().out
        assert user_manager.get_user("alice")["email"] == "new@x.com"

    def test_update_admin_flag(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        args = SimpleNamespace(username="alice", email=None,
                               admin=True, no_admin=False, active=False, inactive=False)
        assert cli._update_user(args) is True
        assert user_manager.is_admin("alice") is True

    def test_update_no_changes(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        args = SimpleNamespace(username="alice", email=None,
                               admin=False, no_admin=False, active=False, inactive=False)
        assert cli._update_user(args) is False
        assert "No updates" in capsys.readouterr().out

    def test_update_nonexistent(self, cli, capsys):
        args = SimpleNamespace(username="nobody", email="x",
                               admin=False, no_admin=False, active=False, inactive=False)
        assert cli._update_user(args) is False
        assert "not found" in capsys.readouterr().out

    def test_update_deactivate(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        args = SimpleNamespace(username="alice", email=None,
                               admin=False, no_admin=False, active=False, inactive=True)
        assert cli._update_user(args) is True


# ---------------------------------------------------------------------------
# UserCLI._change_password
# ---------------------------------------------------------------------------

class TestCLIChangePassword:
    def test_change_with_flags(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "oldpw")
        args = SimpleNamespace(username="alice", old_password="oldpw",
                               new_password="newpw")
        assert cli._change_password(args) is True
        assert "changed successfully" in capsys.readouterr().out
        assert user_manager.validate_credentials("alice", "newpw")

    def test_change_wrong_old_password(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "oldpw")
        args = SimpleNamespace(username="alice", old_password="wrong",
                               new_password="newpw")
        assert cli._change_password(args) is False
        assert "Invalid" in capsys.readouterr().out or "failed" in capsys.readouterr().out

    def test_change_nonexistent_user(self, cli, capsys):
        args = SimpleNamespace(username="nobody", old_password="x", new_password="y")
        assert cli._change_password(args) is False
        assert "not found" in capsys.readouterr().out

    def test_change_prompts(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "oldpw")
        with patch("src.user_cli.getpass.getpass", side_effect=["oldpw", "newpw", "newpw"]):
            args = SimpleNamespace(username="alice", old_password=None, new_password=None)
            assert cli._change_password(args) is True

    def test_change_prompt_mismatch(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "oldpw")
        with patch("src.user_cli.getpass.getpass", side_effect=["oldpw", "aaa", "bbb"]):
            args = SimpleNamespace(username="alice", old_password=None, new_password=None)
            assert cli._change_password(args) is False
        assert "do not match" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# UserCLI._delete_user
# ---------------------------------------------------------------------------

class TestCLIDeleteUser:
    def test_delete_forced(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        args = SimpleNamespace(username="alice", force=True)
        assert cli._delete_user(args) is True
        assert "deleted successfully" in capsys.readouterr().out
        assert not user_manager.user_exists("alice")

    def test_delete_nonexistent(self, cli, capsys):
        args = SimpleNamespace(username="nobody", force=True)
        assert cli._delete_user(args) is False
        assert "not found" in capsys.readouterr().out

    def test_delete_confirm_yes(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        with patch("builtins.input", return_value="y"):
            args = SimpleNamespace(username="alice", force=False)
            assert cli._delete_user(args) is True
        assert "deleted successfully" in capsys.readouterr().out

    def test_delete_confirm_no(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        with patch("builtins.input", return_value="n"):
            args = SimpleNamespace(username="alice", force=False)
            assert cli._delete_user(args) is True  # returns True (cancelled, not error)
        assert "cancelled" in capsys.readouterr().out
        assert user_manager.user_exists("alice")  # user still exists


# ---------------------------------------------------------------------------
# UserCLI._show_info
# ---------------------------------------------------------------------------

class TestCLIShowInfo:
    def test_show_info(self, cli, user_manager, capsys):
        user_manager.create_user("alice", "pw")
        args = SimpleNamespace()
        assert cli._show_info(args) is True
        out = capsys.readouterr().out
        assert "Total users:" in out
        assert "1" in out


# ---------------------------------------------------------------------------
# UserCLI.handle_user_command dispatch
# ---------------------------------------------------------------------------

class TestCLIDispatch:
    def test_no_action(self, cli, capsys):
        args = SimpleNamespace(user_action=None)
        assert cli.handle_user_command(args) is False
        assert "No user action" in capsys.readouterr().out

    def test_unknown_action(self, cli, capsys):
        args = SimpleNamespace(user_action="bogus")
        assert cli.handle_user_command(args) is False
        assert "Unknown" in capsys.readouterr().out

    def test_handles_keyboard_interrupt(self, cli, capsys):
        with patch.object(cli, "_create_user", side_effect=KeyboardInterrupt):
            args = SimpleNamespace(user_action="create")
            assert cli.handle_user_command(args) is False
        assert "cancelled" in capsys.readouterr().out

    def test_dispatches_create(self, cli, capsys):
        args = SimpleNamespace(user_action="create", username="x",
                               password="pw", email="", admin=False)
        assert cli.handle_user_command(args) is True

    def test_dispatches_list(self, cli, capsys):
        args = SimpleNamespace(user_action="list", detailed=False)
        assert cli.handle_user_command(args) is True

    def test_dispatches_info(self, cli, capsys):
        args = SimpleNamespace(user_action="info")
        assert cli.handle_user_command(args) is True


# ---------------------------------------------------------------------------
# cli_entry.py
# ---------------------------------------------------------------------------

class TestCLIEntry:
    def test_cli_entry_imports(self):
        """cli_entry.main should be importable."""
        from cli_entry import main
        assert callable(main)

    def test_manage_users_imports(self):
        """manage_users.main should be importable."""
        from manage_users import main
        assert callable(main)
