from app.extensions import db
from datetime import datetime
from sqlalchemy import Numeric


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
    party_type = db.Column(db.String(20), default="customer")
    # customer | staff | patient

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