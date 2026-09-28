import os
from datetime import datetime
from decimal import Decimal

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



from functools import wraps


def login_required(roles=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in.", "warning")
                return redirect(url_for("login"))
            if roles and session.get("role") not in roles and session.get("role") != "admin":
                flash("Access denied for your role.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator

#Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username, active=True).first()
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


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required()
def dashboard():
    return render_template(
        "dashboard.html",
        category_count=MenuCategory.query.count(),
        item_count=MenuItem.query.filter_by(active=True).count(),
        table_count=RestaurantTable.query.filter_by(active=True).count(),
        open_orders=MealOrder.query.filter(
            MealOrder.status.in_(["draft", "submitted", "in_kitchen", "ready"])
        ).count(),
    )


@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


# Menu 

@app.route("/menu")
@login_required()
def menu_list():
    categories = (
        MenuCategory.query.order_by(MenuCategory.sort_order, MenuCategory.name).all()
    )
    return render_template("menu.html", categories=categories)


@app.route("/menu/categories", methods=["POST"])
@login_required(roles={"admin"})
def menu_category_create():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Category name required.", "danger")
        return redirect(url_for("menu_list"))
    if MenuCategory.query.filter_by(name=name).first():
        flash("Category already exists.", "warning")
        return redirect(url_for("menu_list"))
    sort_order = request.form.get("sort_order", type=int) or 0
    db.session.add(MenuCategory(name=name, sort_order=sort_order))
    db.session.commit()
    flash(f"Category “{name}” added.", "success")
    return redirect(url_for("menu_list"))


@app.route("/menu/items", methods=["POST"])
@login_required(roles={"admin"})
def menu_item_create():
    name = request.form.get("name", "").strip()
    category_id = request.form.get("category_id", type=int)
    try:
        price = Decimal(request.form.get("price", "0").strip() or "0")
    except Exception:
        flash("Invalid price.", "danger")
        return redirect(url_for("menu_list"))
    if not name or not category_id or price < 0:
        flash("Name, category, and valid price required.", "danger")
        return redirect(url_for("menu_list"))
    if not MenuCategory.query.get(category_id):
        flash("Invalid category.", "danger")
        return redirect(url_for("menu_list"))
    item = MenuItem(
        category_id=category_id,
        name=name,
        description=request.form.get("description", "").strip() or None,
        price=price,
        diet_tags=request.form.get("diet_tags", "").strip() or None,
        is_available=True,
        active=True,
    )
    db.session.add(item)
    db.session.commit()
    flash(f"Item “{name}” added.", "success")
    return redirect(url_for("menu_list"))


@app.route("/menu/items/<int:item_id>/toggle", methods=["POST"])
@login_required(roles={"admin"})
def menu_item_toggle(item_id):
    item = MenuItem.query.get_or_404(item_id)
    item.is_available = not item.is_available
    db.session.commit()
    flash(
        f"“{item.name}” is now {'available' if item.is_available else 'unavailable'}.",
        "success",
    )
    return redirect(url_for("menu_list"))

@app.route("/tables")
@login_required()
def tables_list():
    tables = (
        RestaurantTable.query.filter_by(active=True)
        .order_by(RestaurantTable.label)
        .all()
    )
    return render_template("tables.html", tables=tables)


@app.route("/tables", methods=["POST"])
@login_required(roles={"admin"})
def tables_create():
    label = request.form.get("label", "").strip().upper()
    capacity = request.form.get("capacity", type=int) or 4
    if not label:
        flash("Table label required.", "danger")
        return redirect(url_for("tables_list"))
    if RestaurantTable.query.filter_by(label=label).first():
        flash(f"Table {label} already exists.", "warning")
        return redirect(url_for("tables_list"))
    db.session.add(
        RestaurantTable(label=label, capacity=capacity, status="free", active=True)
    )
    db.session.commit()
    flash(f"Table {label} added.", "success")
    return redirect(url_for("tables_list"))


@app.route("/tables/<int:table_id>/status", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def tables_set_status(table_id):
    table = RestaurantTable.query.get_or_404(table_id)
    status = request.form.get("status", "").strip()
    if status not in {"free", "occupied", "reserved"}:
        flash("Invalid status.", "danger")
        return redirect(url_for("tables_list"))
    table.status = status
    db.session.commit()
    flash(f"{table.label} → {status}.", "success")
    return redirect(url_for("tables_list"))

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


@app.route("/orders/open/<int:table_id>", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_open_table(table_id):
    table = RestaurantTable.query.get_or_404(table_id)
    if not table.active:
        flash("Table is inactive.", "danger")
        return redirect(url_for("tables_list"))

    existing = (
        MealOrder.query.filter(
            MealOrder.table_id == table.id,
            MealOrder.status.in_(
                ["draft", "submitted", "in_kitchen", "ready", "delivered"]
            ),
        )
        .order_by(MealOrder.id.desc())
        .first()
    )
    if existing:
        flash(f"{table.label} already has an open order.", "warning")
        return redirect(url_for("order_detail", order_id=existing.id))

    order = MealOrder(
        order_number=next_order_number(),
        order_type="dine_in",
        status="draft",
        table_id=table.id,
        opened_by_id=session.get("user_id"),
    )
    table.status = "occupied"
    db.session.add(order)
    db.session.commit()
    flash(f"Order {order.order_number} opened on {table.label}.", "success")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/orders/<int:order_id>")
@login_required()
def order_detail(order_id):
    order = MealOrder.query.get_or_404(order_id)
    categories = (
        MenuCategory.query.filter_by(active=True)
        .order_by(MenuCategory.sort_order, MenuCategory.name)
        .all()
    )
    return render_template("order_detail.html", order=order, categories=categories)


@app.route("/orders/<int:order_id>/add-item", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_add_item(order_id):
    order = MealOrder.query.get_or_404(order_id)
    if order.status in {"closed", "cancelled"}:
        flash("This order is closed.", "warning")
        return redirect(url_for("order_detail", order_id=order.id))

    item_id = request.form.get("menu_item_id", type=int)
    qty = request.form.get("quantity", type=int) or 1
    if qty < 1:
        qty = 1
    item = MenuItem.query.get(item_id)
    if not item or not item.active or not item.is_available:
        flash("Item not available.", "danger")
        return redirect(url_for("order_detail", order_id=order.id))

    line = MealOrderLine(
        meal_order_id=order.id,
        menu_item_id=item.id,
        item_name=item.name,
        unit_price=item.price,
        quantity=qty,
        line_total=item.price * qty,
        status="queued",
        notes=request.form.get("notes", "").strip()[:255] or None,
    )
    db.session.add(line)
    db.session.flush()
    recalculate_order_totals(order)
    db.session.commit()
    flash(f"Added {qty} × {item.name}.", "success")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/orders/<int:order_id>/submit", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_submit(order_id):
    order = MealOrder.query.get_or_404(order_id)
    active_lines = [ln for ln in order.lines if ln.status != "void"]
    if not active_lines:
        flash("Add at least one item before sending to kitchen.", "danger")
        return redirect(url_for("order_detail", order_id=order.id))
    if order.status not in {"draft", "submitted"}:
        flash("Order cannot be submitted in its current status.", "warning")
        return redirect(url_for("order_detail", order_id=order.id))
    order.status = "submitted"
    db.session.commit()
    flash(f"{order.order_number} sent to kitchen.", "success")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/orders/<int:order_id>/lines/<int:line_id>/void", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_void_line(order_id, line_id):
    order = MealOrder.query.get_or_404(order_id)
    line = MealOrderLine.query.filter_by(
        id=line_id, meal_order_id=order.id
    ).first_or_404()
    line.status = "void"
    recalculate_order_totals(order)
    db.session.commit()
    flash(f"Voided {line.item_name}.", "success")
    return redirect(url_for("order_detail", order_id=order.id))

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