import os
from datetime import datetime
from app.routes.orders import orders_bp
from app.routes.tables import tables_bp
from app.routes.menu import menu_bp
from app.routes.cashier import cashier_bp
from app.routes.dashboard import dashboard_bp
from app.routes.store import store_bp
from decimal import Decimal
from app.routes.auth import auth_bp, login_required
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from app.extensions import db
from sqlalchemy import Numeric, Text
from app.routes.kitchen import kitchen_bp
from app.routes.users import users_bp
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
app.register_blueprint(kitchen_bp)
app.register_blueprint(users_bp)
app.register_blueprint(cashier_bp)
app.register_blueprint(store_bp)




# Create tables + seed admin 

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", full_name="System Admin", role="admin")
        admin.set_password("admin12345")  # change after first login
        db.session.add(admin)
        db.session.commit()
        print("Created admin / admin12345")


if __name__ == "__main__":
    app.run(debug=True)