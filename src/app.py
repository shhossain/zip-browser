"""
Main application entry point for the ZIP file viewer.
"""

import argparse
import sys
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf

from .config import Config
from .auth import AuthManager
from .user_manager import UserManager
from .user_cli import UserCLI, create_user_subparser
from .zip_manager import ZipManager
from .routes import create_routes
from .utils import get_file_icon


def create_main_parser():
    """Create the main argument parser with subcommands"""
    parser = argparse.ArgumentParser(
        description="ZIP File Viewer with Multi-User Authentication"
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Server command (default)
    server_parser = subparsers.add_parser(
        "server", help="Start the web server (default)"
    )
    server_parser.add_argument(
        "zip_paths",
        nargs="+",
        help="Path(s) to ZIP file(s) - can be single files, directories with ZIP files, or URLs",
    )
    server_parser.add_argument(
        "-H",
        "--host",
        default="0.0.0.0",
        help="Host to run the server on (default: 0.0.0.0)",
    )
    server_parser.add_argument(
        "-P",
        "--port",
        type=int,
        default=5000,
        help="Port to run the server on (default: 5000)",
    )
    server_parser.add_argument(
        "-D", "--debug", action="store_true", help="Enable debug mode"
    )

    # Legacy single-user mode (for backward compatibility)
    server_parser.add_argument(
        "-u", "--username", help="Username for single-user mode (legacy)"
    )
    server_parser.add_argument(
        "-p", "--password", help="Password for single-user mode (legacy)"
    )

    # User management command
    create_user_subparser(subparsers)

    return parser


def create_app(config=None):
    """Create and configure the Flask application"""
    if config is None:
        parser = create_main_parser()

        # Check if we need to insert 'server' command for backward compatibility
        if (
            len(sys.argv) > 1
            and sys.argv[1] not in ["server", "user"]
            and not sys.argv[1].startswith("-")
        ):
            # First argument looks like zip path, add server command
            sys.argv.insert(1, "server")

        args = parser.parse_args()

        # Handle subcommands
        if args.command == "user":
            user_cli = UserCLI()
            success = user_cli.handle_user_command(args)
            sys.exit(0 if success else 1)
        elif args.command is None:
            parser.print_help()
            sys.exit(1)

        if args.command != "server":
            parser.print_help()
            sys.exit(1)

        config = Config.from_args(args)

    # Create Flask app
    app = Flask(__name__)
    app.secret_key = config.secret_key

    # Enable CSRF protection
    csrf = CSRFProtect(app)

    # Configure static files
    app.static_folder = "static"

    # Initialize managers
    if config.multiuser:
        # New multiuser system
        user_manager = UserManager()
        auth_manager = AuthManager(user_manager)

        # Check if any users exist, if not, prompt to create admin user
        if user_manager.get_user_count() == 0:
            print("\nNo users found in the system.")
            print("Please create an admin user using the user management command:")
            print("  python -m src.app user create <username> --admin")
            print("  or")
            print("  python main.py user create <username> --admin")
            sys.exit(1)
    else:
        # Legacy single-user mode
        print(
            "Warning: Using legacy single-user mode. Consider migrating to multiuser system."
        )
        from .auth import AuthManager as LegacyAuthManager

        class LegacyAuthManager:
            def __init__(self, username, password):
                self.username = username
                self.password = password

            def validate_credentials(self, username, password):
                return username == self.username and password == self.password

            def load_user(self, user_id):
                from .auth import User

                if user_id == self.username:
                    return User(user_id)
                return None

        auth_manager = LegacyAuthManager(config.username, config.password)

    zip_manager = ZipManager()
    zip_manager.initialize_zip_files(config.zip_paths)

    # Setup Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "main.login"
    login_manager.login_message = "Please log in to access the ZIP file browser."

    @login_manager.user_loader
    def load_user(user_id):
        return auth_manager.load_user(user_id)

    # Add utility functions to template context
    @app.context_processor
    def utility_processor():
        return dict(get_file_icon=get_file_icon)

    # Expose CSRF token helper in templates for AJAX forms
    app.jinja_env.globals["csrf_token"] = generate_csrf

    # Register routes
    routes_bp = create_routes(auth_manager, zip_manager)
    app.register_blueprint(routes_bp)

    # Store config for access in other parts of the app
    app.config["APP_CONFIG"] = config

    return app


def main():
    """Main entry point for server command"""
    parser = create_main_parser()

    # Check if we need to insert 'server' command for backward compatibility
    if (
        len(sys.argv) > 1
        and sys.argv[1] not in ["server", "user"]
        and not sys.argv[1].startswith("-")
    ):
        # First argument looks like zip path, add server command
        sys.argv.insert(1, "server")

    args = parser.parse_args()

    # Handle user command directly here if needed
    if args.command == "user":
        user_cli = UserCLI()
        success = user_cli.handle_user_command(args)
        sys.exit(0 if success else 1)
    elif args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command != "server":
        parser.print_help()
        sys.exit(1)

    config = Config.from_args(args)
    app = create_app(config)
    app.run(host=config.host, port=config.port, debug=config.debug)


if __name__ == "__main__":
    main()
