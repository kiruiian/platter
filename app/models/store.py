from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Numeric

from app.extensions import db


class StoreItem(db.Model):
    __tablename__ = "store_items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(20), nullable=False, default="kg")
    quantity_on_hand = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    min_qty = db.Column(db.Numeric(12, 3), default=0)
    active = db.Column(db.Boolean, default=True)

    movements = db.relationship("StockMovement", backref="item", lazy="dynamic")


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("store_items.id"), nullable=False)
    movement_type = db.Column(db.String(10), nullable=False)  # in | out
    quantity = db.Column(db.Numeric(12, 3), nullable=False)
    reason = db.Column(db.String(30), nullable=False)  # purchase | kitchen_issue | adjustment
    reference = db.Column(db.String(120))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    recorded_by = db.relationship("User")


class Supplier(db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    contact_name = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(120))
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    purchases = db.relationship(
        "StockPurchase",
        back_populates="supplier",
        lazy="dynamic",
    )


class StockPurchase(db.Model):
    __tablename__ = "stock_purchases"

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    invoice_number = db.Column(db.String(120))
    purchased_on = db.Column(db.Date, nullable=False, default=date.today)
    notes = db.Column(db.String(255))
    received_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    supplier = db.relationship("Supplier", back_populates="purchases")
    received_by = db.relationship("User")
    lines = db.relationship(
        "StockPurchaseLine",
        back_populates="purchase",
        cascade="all, delete-orphan",
        order_by="StockPurchaseLine.id",
    )

    @property
    def total(self) -> Decimal:
        return sum((line.line_total or Decimal("0")) for line in self.lines)


class StockPurchaseLine(db.Model):
    __tablename__ = "stock_purchase_lines"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(
        db.Integer,
        db.ForeignKey("stock_purchases.id"),
        nullable=False,
    )
    item_id = db.Column(db.Integer, db.ForeignKey("store_items.id"), nullable=False)
    quantity = db.Column(Numeric(12, 3), nullable=False)
    unit_cost = db.Column(Numeric(12, 2), nullable=False)
    line_total = db.Column(Numeric(12, 2), nullable=False)

    purchase = db.relationship("StockPurchase", back_populates="lines")
    item = db.relationship("StoreItem", backref="purchase_lines")
