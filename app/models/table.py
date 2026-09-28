from app.extensions import db

class RestaurantTable(db.Model):
    __tablename__ = "restaurant_tables"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(30), unique=True, nullable=False)  # T1, T2
    capacity = db.Column(db.Integer, default=4)
    status = db.Column(db.String(20), default="free")  # free | occupied | reserved
    active = db.Column(db.Boolean, default=True)

    meal_orders = db.relationship("MealOrder", back_populates="table")