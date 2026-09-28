from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash
from app.extensions import db

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
    