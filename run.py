import os
from datetime import datetime
from app.routes.orders import orders_bp
from app.routes.tables import tables_bp
from app.routes.menu import menu_bp
from app.routes.dashboard import dashboard_bp
from decimal import Decimal
from app.routes.auth import auth_bp, login_required
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from app.extensions import db
from sqlalchemy import Numeric, Text
from werkzeug.security import check_password_hash, generate_password_hash
from app.models import (
    User,
    MenuCategory,
    MenuItem,
    RestaurantTable,
    MealOrder,
    MealOrderLine,
    Payment,
)

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/platter",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(menu_bp)
app.register_blueprint(tables_bp)
app.register_blueprint(orders_bp)






# Helpers

def next_order_number() -> str:
    """Simple daily sequence: PL-20260925-0001"""
    today = datetime.utcnow().strftime("%Y%m%d")
    prefix = f"PL-{today}-"
    last = (
        MealOrder.query.filter(MealOrder.order_number.like(f"{prefix}%"))
        .order_by(MealOrder.id.desc())
        .first()
    )
    seq = 1
    if last and last.order_number:
        try:
            seq = int(last.order_number.split("-")[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


def recalculate_order_totals(order: MealOrder) -> None:
    sub = sum((line.line_total or 0) for line in order.lines if line.status != "void")
    order.subtotal = sub
    order.tax = Decimal("0.00")  # add tax rules later
    order.total = (order.subtotal or 0) + (order.tax or 0)


# Create tables + seed admin 

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", full_name="System Admin", role="admin")
        admin.set_password("admin12345")  # change after first login
        db.session.add(admin)
        db.session.commit()
        print("Created admin / admin12345")





@app.route("/orders")
@login_required()
def orders_list():
    orders = (
        MealOrder.query.filter(
            MealOrder.status.in_(
                ["draft", "submitted", "in_kitchen", "ready", "delivered"]
            )
        )
        .order_by(MealOrder.opened_at.desc())
        .limit(50)
        .all()
    )
    return render_template("orders.html", orders=orders)













@app.route("/kitchen")
@login_required(roles={"admin", "kitchen"})
def kitchen_board():
    orders = (
        MealOrder.query.filter(
            MealOrder.status.in_(["submitted", "in_kitchen", "ready"])
        )
        .order_by(MealOrder.opened_at.asc())
        .all()
    )
    return render_template("kitchen.html", orders=orders)


@app.route("/orders/<int:order_id>/kitchen-status", methods=["POST"])
@login_required(roles={"admin", "kitchen"})
def order_kitchen_status(order_id):
    order = MealOrder.query.get_or_404(order_id)
    status = request.form.get("status", "").strip()
    if status not in {"in_kitchen", "ready"}:
        flash("Invalid kitchen status.", "danger")
        return redirect(url_for("kitchen_board"))
    if order.status not in {"submitted", "in_kitchen", "ready"}:
        flash("Order is not in the kitchen queue.", "warning")
        return redirect(url_for("kitchen_board"))
    order.status = status
    db.session.commit()
    flash(f"{order.order_number} → {status.replace('_', ' ')}.", "success")
    return redirect(url_for("kitchen_board"))

VALID_ROLES = ("admin", "waiter", "kitchen", "cashier", "ward_attendant")


@app.route("/users")
@login_required(roles={"admin"})
def users_list():
    users = User.query.order_by(User.username).all()
    return render_template("users.html", users=users, roles=VALID_ROLES)


@app.route("/users", methods=["POST"])
@login_required(roles={"admin"})
def users_create():
    username = request.form.get("username", "").strip().lower()
    full_name = request.form.get("full_name", "").strip()
    role = request.form.get("role", "").strip()
    password = request.form.get("password", "")

    if not username or not full_name or not password:
        flash("Username, full name, and password are required.", "danger")
        return redirect(url_for("users_list"))
    if role not in VALID_ROLES:
        flash("Invalid role.", "danger")
        return redirect(url_for("users_list"))
    if User.query.filter_by(username=username).first():
        flash("Username already exists.", "warning")
        return redirect(url_for("users_list"))

    user = User(username=username, full_name=full_name, role=role, active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f"User {username} ({role}) created.", "success")
    return redirect(url_for("users_list"))


@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required(roles={"admin"})
def users_toggle(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == session.get("user_id"):
        flash("You cannot deactivate yourself.", "warning")
        return redirect(url_for("users_list"))
    user.active = not user.active
    db.session.commit()
    flash(
        f"{user.username} is now {'active' if user.active else 'inactive'}.",
        "success",
    )
    return redirect(url_for("users_list"))

if __name__ == "__main__":
    app.run(debug=True)