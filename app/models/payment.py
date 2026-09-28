from app.extensions import db
from datetime import datetime
from sqlalchemy import Numeric


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    meal_order_id = db.Column(db.Integer, db.ForeignKey("meal_orders.id"), nullable=False)
    amount = db.Column(Numeric(10, 2), nullable=False)
    method = db.Column(db.String(30), nullable=False)  # cash | mpesa | card | charge_to_ward
    reference = db.Column(db.String(80))
    received_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    tendered = db.Column(Numeric(10, 2))      # cash given
    change_given = db.Column(Numeric(10, 2))  # change returned

    meal_order = db.relationship("MealOrder", back_populates="payments")
    received_by = db.relationship("User", back_populates="payments")
