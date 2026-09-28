from flask import Blueprint, redirect, render_template, session, url_for

from app.models import MenuCategory, MenuItem, RestaurantTable, MealOrder
from app.routes.auth import login_required


dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard.dashboard"))
    return redirect(url_for("auth.login"))


@dashboard_bp.route("/dashboard")
@login_required()
def dashboard():
    return render_template(
        "dashboard.html",
        category_count=MenuCategory.query.count(),
        item_count=MenuItem.query.filter_by(active=True).count(),
        table_count=RestaurantTable.query.filter_by(active=True).count(),
        open_orders=MealOrder.query.filter(
            MealOrder.status.in_(
                ["draft", "submitted", "in_kitchen", "ready"]
            )
        ).count(),
    )