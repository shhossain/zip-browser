"""
Command-line interface for user management.
"""
import argparse
import getpass
import sys
from typing import Optional
from tabulate import tabulate

from .user_manager import UserManager


def create_user_subparser(subparsers):
    """Create the user management subcommand parser"""
    user_parser = subparsers.add_parser('user', help='User management commands')
    user_subparsers = user_parser.add_subparsers(dest='user_action', help='User actions')
    
    # Create user
    create_parser = user_subparsers.add_parser('create', help='Create a new user')
    create_parser.add_argument('username', help='Username for the new user')
    create_parser.add_argument('-e', '--email', help='Email address (optional)')
    create_parser.add_argument('-a', '--admin', action='store_true', 
                              help='Make user an administrator')
    create_parser.add_argument('-p', '--password', 
                              help='Password (will prompt if not provided)')
    
    # List users
    list_parser = user_subparsers.add_parser('list', help='List all users')
    list_parser.add_argument('--detailed', action='store_true',
                            help='Show detailed user information')
    
    # Show user
    show_parser = user_subparsers.add_parser('show', help='Show user details')
    show_parser.add_argument('username', help='Username to show')
    
    # Update user
    update_parser = user_subparsers.add_parser('update', help='Update user information')
    update_parser.add_argument('username', help='Username to update')
    update_parser.add_argument('-e', '--email', help='New email address')
    update_parser.add_argument('-a', '--admin', action='store_true',
                              help='Make user an administrator')
    update_parser.add_argument('--no-admin', action='store_true',
                              help='Remove administrator privileges')
    update_parser.add_argument('--active', action='store_true',
                              help='Activate user account')
    update_parser.add_argument('--inactive', action='store_true',
                              help='Deactivate user account')
    
    # Change password
    passwd_parser = user_subparsers.add_parser('passwd', help='Change user password')
    passwd_parser.add_argument('username', help='Username to change password for')
    passwd_parser.add_argument('--old-password', 
                              help='Current password (will prompt if not provided)')
    passwd_parser.add_argument('--new-password',
                              help='New password (will prompt if not provided)')
    
    # Delete user
    delete_parser = user_subparsers.add_parser('delete', help='Delete a user')
    delete_parser.add_argument('username', help='Username to delete')
    delete_parser.add_argument('-f', '--force', action='store_true',
                              help='Force deletion without confirmation')
    
    # Info command
    info_parser = user_subparsers.add_parser('info', help='Show user database information')


class UserCLI:
    """Command-line interface for user management"""
    
    def __init__(self):
        self.user_manager = UserManager()
    
    def handle_user_command(self, args):
        """Handle user management commands"""
        if not args.user_action:
            print("Error: No user action specified. Use -h for help.")
            return False
        
        try:
            if args.user_action == 'create':
                return self._create_user(args)
            elif args.user_action == 'list':
                return self._list_users(args)
            elif args.user_action == 'show':
                return self._show_user(args)
            elif args.user_action == 'update':
                return self._update_user(args)
            elif args.user_action == 'passwd':
                return self._change_password(args)
            elif args.user_action == 'delete':
                return self._delete_user(args)
            elif args.user_action == 'info':
                return self._show_info(args)
            else:
                print(f"Error: Unknown user action '{args.user_action}'")
                return False
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            return False
        except Exception as e:
            print(f"Error: {e}")
            return False
    
    def _create_user(self, args) -> bool:
        """Create a new user"""
        username = args.username
        
        if self.user_manager.user_exists(username):
            print(f"Error: User '{username}' already exists.")
            return False
        
        # Get password
        if args.password:
            password = args.password
        else:
            password = getpass.getpass("Enter password: ")
            confirm_password = getpass.getpass("Confirm password: ")
            
            if password != confirm_password:
                print("Error: Passwords do not match.")
                return False
        
        if not password:
            print("Error: Password cannot be empty.")
            return False
        
        # Create user
        success = self.user_manager.create_user(
            username=username,
            password=password,
            email=args.email or "",
            is_admin=args.admin
        )
        
        if success:
            print(f"User '{username}' created successfully.")
            if args.admin:
                print(f"User '{username}' has administrator privileges.")
            return True
        else:
            print(f"Error: Failed to create user '{username}'.")
            return False
    
    def _list_users(self, args) -> bool:
        """List all users"""
        users = self.user_manager.list_users()
        
        if not users:
            print("No users found.")
            return True
        
        if args.detailed:
            # Detailed view
            headers = ['Username', 'Email', 'Admin', 'Active', 'Created', 'Last Login']
            rows = []
            
            for user in users:
                rows.append([
                    user['username'],
                    user.get('email', ''),
                    '✓' if user.get('is_admin', False) else '',
                    '✓' if user.get('active', True) else '✗',
                    user.get('created_at', '')[:19] if user.get('created_at') else '',
                    user.get('last_login', '')[:19] if user.get('last_login') else 'Never'
                ])
        else:
            # Simple view
            headers = ['Username', 'Admin', 'Active']
            rows = []
            
            for user in users:
                rows.append([
                    user['username'],
                    '✓' if user.get('is_admin', False) else '',
                    '✓' if user.get('active', True) else '✗'
                ])
        
        print(tabulate(rows, headers=headers, tablefmt='grid'))
        print(f"\nTotal users: {len(users)}")
        return True
    
    def _show_user(self, args) -> bool:
        """Show detailed user information"""
        username = args.username
        user = self.user_manager.get_user(username)
        
        if not user:
            print(f"Error: User '{username}' not found.")
            return False
        
        print(f"User Information for '{username}':")
        print("-" * 40)
        print(f"Username:      {user['username']}")
        print(f"Email:         {user.get('email', 'Not set')}")
        print(f"Administrator: {'Yes' if user.get('is_admin', False) else 'No'}")
        print(f"Active:        {'Yes' if user.get('active', True) else 'No'}")
        print(f"Created:       {user.get('created_at', 'Unknown')}")
        print(f"Updated:       {user.get('updated_at', 'Unknown')}")
        print(f"Last Login:    {user.get('last_login', 'Never')}")
        
        return True
    
    def _update_user(self, args) -> bool:
        """Update user information"""
        username = args.username
        
        if not self.user_manager.user_exists(username):
            print(f"Error: User '{username}' not found.")
            return False
        
        updates = {}
        
        if args.email is not None:
            updates['email'] = args.email
        
        if args.admin:
            updates['is_admin'] = True
        elif args.no_admin:
            updates['is_admin'] = False
        
        if args.active:
            updates['active'] = True
        elif args.inactive:
            updates['active'] = False
        
        if not updates:
            print("Error: No updates specified.")
            return False
        
        success = self.user_manager.update_user(username, **updates)
        
        if success:
            print(f"User '{username}' updated successfully.")
            return True
        else:
            print(f"Error: Failed to update user '{username}'.")
            return False
    
    def _change_password(self, args) -> bool:
        """Change user password"""
        username = args.username
        
        if not self.user_manager.user_exists(username):
            print(f"Error: User '{username}' not found.")
            return False
        
        # Get old password
        if args.old_password:
            old_password = args.old_password
        else:
            old_password = getpass.getpass("Enter current password: ")
        
        # Get new password
        if args.new_password:
            new_password = args.new_password
        else:
            new_password = getpass.getpass("Enter new password: ")
            confirm_password = getpass.getpass("Confirm new password: ")
            
            if new_password != confirm_password:
                print("Error: Passwords do not match.")
                return False
        
        if not new_password:
            print("Error: New password cannot be empty.")
            return False
        
        success = self.user_manager.change_password(username, old_password, new_password)
        
        if success:
            print(f"Password changed successfully for user '{username}'.")
            return True
        else:
            print("Error: Invalid current password or failed to update password.")
            return False
    
    def _delete_user(self, args) -> bool:
        """Delete a user"""
        username = args.username
        
        if not self.user_manager.user_exists(username):
            print(f"Error: User '{username}' not found.")
            return False
        
        # Confirmation
        if not args.force:
            response = input(f"Are you sure you want to delete user '{username}'? (y/N): ")
            if response.lower() not in ['y', 'yes']:
                print("Deletion cancelled.")
                return True
        
        success = self.user_manager.delete_user(username)
        
        if success:
            print(f"User '{username}' deleted successfully.")
            return True
        else:
            print(f"Error: Failed to delete user '{username}'.")
            return False
    
    def _show_info(self, args) -> bool:
        """Show user database information"""
        print("User Database Information:")
        print("-" * 30)
        print(f"Database file: {self.user_manager.get_users_file_location()}")
        print(f"Total users:   {self.user_manager.get_user_count()}")
        
        return True
