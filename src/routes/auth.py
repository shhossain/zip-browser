"""
Authentication routes for login/logout.
"""

from flask import (
    Blueprint,
    request,
    render_template,
    redirect,
    url_for,
    flash,
)
from flask_login import login_user, logout_user, login_required, current_user

from ..auth import LoginForm


def create_auth_routes(auth_manager):
    """Create authentication routes."""
    bp = Blueprint("auth", __name__)

    @bp.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("browse.zip_list"))
        return redirect(url_for("auth.login"))

    @bp.route("/login", methods=["GET", "POST"])
    def login():
        form = LoginForm()

        if form.validate_on_submit():
            username = form.username.data
            password = form.password.data

            if auth_manager.validate_credentials(username, password):
                user = auth_manager.load_user(username)
                login_user(user)
                next_page = request.args.get("next")
                return redirect(next_page or url_for("browse.zip_list"))
            else:
                flash("Invalid username or password")

        return render_template("login.html", form=form)

    @bp.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out")
        return redirect(url_for("auth.login"))

    return bp
