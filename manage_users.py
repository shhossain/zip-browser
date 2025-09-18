#!/usr/bin/env python3
"""
User management utility for ZIP File Viewer
"""

import argparse
import sys
from src.user_cli import UserCLI


def main():
    """Main entry point for user management"""
    parser = argparse.ArgumentParser(description="ZIP File Viewer - User Management")
    subparsers = parser.add_subparsers(dest="user_action", help="User actions")

    # Create user
    create_parser = subparsers.add_parser("create", help="Create a new user")
    create_parser.add_argument("username", help="Username for the new user")
    create_parser.add_argument("-e", "--email", help="Email address (optional)")
    create_parser.add_argument(
        "-a", "--admin", action="store_true", help="Make user an administrator"
    )
    create_parser.add_argument(
        "-p", "--password", help="Password (will prompt if not provided)"
    )

    # List users
    list_parser = subparsers.add_parser("list", help="List all users")
    list_parser.add_argument(
        "--detailed", action="store_true", help="Show detailed user information"
    )

    # Show user
    show_parser = subparsers.add_parser("show", help="Show user details")
    show_parser.add_argument("username", help="Username to show")

    # Update user
    update_parser = subparsers.add_parser("update", help="Update user information")
    update_parser.add_argument("username", help="Username to update")
    update_parser.add_argument("-e", "--email", help="New email address")
    update_parser.add_argument(
        "-a", "--admin", action="store_true", help="Make user an administrator"
    )
    update_parser.add_argument(
        "--no-admin", action="store_true", help="Remove administrator privileges"
    )
    update_parser.add_argument(
        "--active", action="store_true", help="Activate user account"
    )
    update_parser.add_argument(
        "--inactive", action="store_true", help="Deactivate user account"
    )

    # Change password
    passwd_parser = subparsers.add_parser("passwd", help="Change user password")
    passwd_parser.add_argument("username", help="Username to change password for")
    passwd_parser.add_argument(
        "--old-password", help="Current password (will prompt if not provided)"
    )
    passwd_parser.add_argument(
        "--new-password", help="New password (will prompt if not provided)"
    )

    # Delete user
    delete_parser = subparsers.add_parser("delete", help="Delete a user")
    delete_parser.add_argument("username", help="Username to delete")
    delete_parser.add_argument(
        "-f", "--force", action="store_true", help="Force deletion without confirmation"
    )

    # Info command
    info_parser = subparsers.add_parser("info", help="Show user database information")

    if len(sys.argv) == 1:
        parser.print_help()
        return

    try:
        args = parser.parse_args()
    except SystemExit:
        return

    user_cli = UserCLI()
    success = user_cli.handle_user_command(args)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
