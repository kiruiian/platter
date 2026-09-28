from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import MealOrder
from app.routes.auth import login_required

kitchen_bp = Blueprint("kitchen", __name__)


@kitchen_bp.route("/kitchen")
@login_required(roles={"admin", "kitchen"})
def kitchen_board():
    orders = (
        MealOrder.query.filter(
            MealOrder.status.in_(["submitted", "in_kitchen", "ready"])
        )
        .order_by(MealOrder.opened_at.asc())
        .all()
    )
    return render_template("kitchen.html", orders=orders)


@kitchen_bp.route("/orders/<int:order_id>/kitchen-status", methods=["POST"])
@login_required(roles={"admin", "kitchen"})
def order_kitchen_status(order_id):
    order = MealOrder.query.get_or_404(order_id)

    status = request.form.get("status", "").strip()

    if status not in {"in_kitchen", "ready"}:
        flash("Invalid kitchen status.", "danger")
        return redirect(url_for("kitchen.kitchen_board"))

    if order.status not in {"submitted", "in_kitchen", "ready"}:
        flash("Order is not in the kitchen queue.", "warning")
        return redirect(url_for("kitchen.kitchen_board"))

    order.status = status
    db.session.commit()

    flash(
        f"{order.order_number} → {status.replace('_', ' ')}.",
        "success",
    )

    return redirect(url_for("kitchen.kitchen_board"))