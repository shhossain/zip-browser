"""
Authentication and user management functionality.
"""
from flask_login import UserMixin
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired

from .user_manager import UserManager


class User(UserMixin):
    """User class for authentication with Flask-Login"""

    def __init__(self, username, email=None, is_admin=False):
        self.id = username
        self.username = username
        self.email = email or ""
        self.is_admin = is_admin


class LoginForm(FlaskForm):
    """Login form with CSRF protection"""
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class AuthManager:
    """Manages authentication operations using the UserManager"""

    def __init__(self, user_manager=None):
        self.user_manager = user_manager or UserManager()

    def validate_credentials(self, username, password):
        """Validate user credentials"""
        if self.user_manager.validate_credentials(username, password):
            # Update last login timestamp
            self.user_manager.update_last_login(username)
            return True
        return False

    def load_user(self, user_id):
        """Load user by ID for Flask-Login"""
        user_info = self.user_manager.get_user(user_id)
        if user_info and user_info.get("active", True):
            return User(
                username=user_info["username"],
                email=user_info.get("email", ""),
                is_admin=user_info.get("is_admin", False),
            )
        return None

    def get_user_info(self, username):
        """Get user information"""
        return self.user_manager.get_user(username)

    def is_admin(self, username):
        """Check if user is an admin"""
        return self.user_manager.is_admin(username)
