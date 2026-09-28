from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import User
from app.routes.auth import login_required


users_bp = Blueprint("users", __name__)


VALID_ROLES = (
    "admin",
    "waiter",
    "kitchen",
    "cashier",
    "ward_attendant",
)


@users_bp.route("/users")
@login_required(roles={"admin"})
def users_list():
    users = User.query.order_by(User.username).all()
    return render_template("users.html", users=users, roles=VALID_ROLES)


@users_bp.route("/users", methods=["POST"])
@login_required(roles={"admin"})
def users_create():
    username = request.form.get("username", "").strip().lower()
    full_name = request.form.get("full_name", "").strip()
    role = request.form.get("role", "").strip()
    password = request.form.get("password", "")

    if not username or not full_name or not password:
        flash("Username, full name, and password are required.", "danger")
        return redirect(url_for("users.users_list"))

    if role not in VALID_ROLES:
        flash("Invalid role.", "danger")
        return redirect(url_for("users.users_list"))

    if User.query.filter_by(username=username).first():
        flash("Username already exists.", "warning")
        return redirect(url_for("users.users_list"))

    user = User(
        username=username,
        full_name=full_name,
        role=role,
        active=True,
    )

    user.set_password(password)

    db.session.add(user)
    db.session.commit()

    flash(f"User {username} ({role}) created.", "success")
    return redirect(url_for("users.users_list"))


@users_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required(roles={"admin"})
def users_toggle(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == session.get("user_id"):
        flash("You cannot deactivate yourself.", "warning")
        return redirect(url_for("users.users_list"))

    user.active = not user.active
    db.session.commit()

    flash(
        f"{user.username} is now "
        f"{'active' if user.active else 'inactive'}.",
        "success",
    )

    return redirect(url_for("users.users_list"))