from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, session, url_for

from app.models import MealOrder, MenuCategory, MenuItem, Payment, RestaurantTable
from app.routes.auth import login_required


dashboard_bp = Blueprint("dashboard", __name__)

OPEN_STATUSES = ("draft", "submitted", "in_kitchen", "ready", "delivered")


@dashboard_bp.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard.dashboard"))
    return redirect(url_for("auth.login"))


@dashboard_bp.route("/dashboard")
@login_required()
def dashboard():
    open_orders = (
        MealOrder.query.filter(MealOrder.status.in_(OPEN_STATUSES))
        .order_by(MealOrder.opened_at.asc())
        .all()
    )
    start = datetime.combine(datetime.now().date(), datetime.min.time())
    payments = Payment.query.filter(
        Payment.received_at >= start,
        Payment.received_at < start + timedelta(days=1),
    ).all()
    confirmed = [p for p in payments if (p.result_desc or "") != "pending"]
    takings = sum(float(p.amount or 0) for p in confirmed)
    return render_template(
        "dashboard.html",
        category_count=MenuCategory.query.count(),
        item_count=MenuItem.query.filter_by(active=True).count(),
        table_count=RestaurantTable.query.filter_by(active=True).count(),
        open_orders=open_orders,
        open_count=len(open_orders),
        takings=takings,
    )