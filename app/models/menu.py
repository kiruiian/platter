from sqlalchemy import Numeric, Text
from app.extensions import db


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
