from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.models import User


auth_bp = Blueprint("auth", __name__)


def login_required(roles=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in.", "warning")
                return redirect(url_for("auth.login"))

            if roles and session.get("role") not in roles and session.get("role") != "admin":
                flash("Access denied for your role.", "danger")
                return redirect(url_for("dashboard"))

            return view(*args, **kwargs)

        return wrapped

    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(
            username=username,
            active=True
        ).first()

        if user and user.check_password(password):
            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role
            session["full_name"] = user.full_name

            flash(f"Welcome, {user.full_name}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("auth.login"))