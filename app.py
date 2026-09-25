import os
from datetime import datetime
from decimal import Decimal

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Numeric, Text
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/platter",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


#Models 

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="waiter")
    # admin | waiter | cashier | kitchen | ward_attendant
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    meal_orders = db.relationship("MealOrder", back_populates="opened_by", foreign_keys="MealOrder.opened_by_id")
    payments = db.relationship("Payment", back_populates="received_by")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class MenuCategory(db.Model):
    __tablename__ = "menu_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)

    items = db.relationship("MenuItem", back_populates="category", lazy="dynamic")


class MenuItem(db.Model):
    __tablename__ = "menu_items"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("menu_categories.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(Numeric(10, 2), nullable=False)
    is_available = db.Column(db.Boolean, default=True)
    # For ward later: normal, soft, diabetic, etc. (comma-separated or single tag in v1)
    diet_tags = db.Column(db.String(120))
    active = db.Column(db.Boolean, default=True)

    category = db.relationship("MenuCategory", back_populates="items")
    order_lines = db.relationship("MealOrderLine", back_populates="menu_item")


class RestaurantTable(db.Model):
    __tablename__ = "restaurant_tables"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(30), unique=True, nullable=False)  # T1, T2
    capacity = db.Column(db.Integer, default=4)
    status = db.Column(db.String(20), default="free")  # free | occupied | reserved
    active = db.Column(db.Boolean, default=True)

    meal_orders = db.relationship("MealOrder", back_populates="table")


class MealOrder(db.Model):
    """
    One order = one ticket for kitchen / bill.
    order_type: dine_in | takeaway | ward
    """
    __tablename__ = "meal_orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(30), unique=True, nullable=False, index=True)

    order_type = db.Column(db.String(20), nullable=False, default="dine_in")
    status = db.Column(db.String(20), nullable=False, default="draft")
    # draft | submitted | in_kitchen | ready | delivered | closed | cancelled

    table_id = db.Column(db.Integer, db.ForeignKey("restaurant_tables.id"), nullable=True)
    opened_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)

    # Takeaway
    customer_name = db.Column(db.String(120))
    customer_phone = db.Column(db.String(40))

    # (HMIS-ready)
    ward_name = db.Column(db.String(80))
    bed_label = db.Column(db.String(30))
    external_patient_id = db.Column(db.String(60))  # HMIS id later
    patient_name = db.Column(db.String(120))
    diet_type = db.Column(db.String(40))  # normal | soft | diabetic | ...
    meal_slot = db.Column(db.String(20))  # breakfast | lunch | dinner | snack

    notes = db.Column(db.Text)
    subtotal = db.Column(Numeric(10, 2), default=0)
    tax = db.Column(Numeric(10, 2), default=0)
    total = db.Column(Numeric(10, 2), default=0)

    table = db.relationship("RestaurantTable", back_populates="meal_orders")
    opened_by = db.relationship("User", back_populates="meal_orders", foreign_keys=[opened_by_id])
    lines = db.relationship(
        "MealOrderLine",
        back_populates="meal_order",
        cascade="all, delete-orphan",
        order_by="MealOrderLine.id",
    )
    payments = db.relationship("Payment", back_populates="meal_order", cascade="all, delete-orphan")


class MealOrderLine(db.Model):
    __tablename__ = "meal_order_lines"

    id = db.Column(db.Integer, primary_key=True)
    meal_order_id = db.Column(db.Integer, db.ForeignKey("meal_orders.id"), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), nullable=True)

    # Snapshots — bill stays correct if menu price changes later
    item_name = db.Column(db.String(120), nullable=False)
    unit_price = db.Column(Numeric(10, 2), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    line_total = db.Column(Numeric(10, 2), nullable=False)

    status = db.Column(db.String(20), default="queued")  # queued | preparing | done | void
    notes = db.Column(db.String(255))

    meal_order = db.relationship("MealOrder", back_populates="lines")
    menu_item = db.relationship("MenuItem", back_populates="order_lines")


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    meal_order_id = db.Column(db.Integer, db.ForeignKey("meal_orders.id"), nullable=False)
    amount = db.Column(Numeric(10, 2), nullable=False)
    method = db.Column(db.String(30), nullable=False)  # cash | mpesa | card | charge_to_ward
    reference = db.Column(db.String(80))
    received_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)

    meal_order = db.relationship("MealOrder", back_populates="payments")
    received_by = db.relationship("User", back_populates="payments")


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

if __name__ == "__main__":
    app.run(debug=True)