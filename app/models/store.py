from datetime import datetime
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