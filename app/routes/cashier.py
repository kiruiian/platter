from datetime import datetime
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import MealOrder, Payment
from app.routes.auth import login_required

cashier_bp = Blueprint("cashier", __name__)

PAYMENT_METHODS = ("cash", "mpesa", "card", "charge_to_ward")


@cashier_bp.route("/cashier")
@login_required(roles={"admin", "cashier"})
def cashier_board():
    orders = (
        MealOrder.query.filter(MealOrder.status.in_(["ready", "delivered"]))
        .order_by(MealOrder.opened_at.asc())
        .all()
    )
    return render_template("cashier.html", orders=orders)


@cashier_bp.route("/cashier/<int:order_id>/pay", methods=["POST"])
@login_required(roles={"admin", "cashier"})
def cashier_pay(order_id):
    order = MealOrder.query.get_or_404(order_id)
    if order.status not in {"ready", "delivered"}:
        flash("This order is not ready for payment.", "warning")
        return redirect(url_for("cashier.cashier_board"))

    method = request.form.get("method", "").strip()
    if method not in PAYMENT_METHODS:
        flash("Choose a valid payment method.", "danger")
        return redirect(url_for("cashier.cashier_board"))

    already_paid = sum(float(p.amount or 0) for p in order.payments)
    due = round(float(order.total or 0) - already_paid, 2)
    if due <= 0:
        flash("Order is already fully paid.", "warning")
        return redirect(url_for("cashier.cashier_board"))

    tendered = None
    change_given = None
    amount = due

    if method == "cash":
        raw_tendered = request.form.get("tendered", "").strip()
        try:
            tendered = float(raw_tendered)
        except ValueError:
            flash("Enter cash received.", "danger")
            return redirect(url_for("cashier.cashier_board"))
        if tendered + 0.001 < due:
            flash(f"Cash received is less than balance KES {due:.2f}.", "danger")
            return redirect(url_for("cashier.cashier_board"))
        amount = due
        change_given = round(tendered - due, 2)
    else:
        raw = request.form.get("amount", "").strip()
        try:
            amount = float(raw) if raw else due
        except ValueError:
            flash("Enter a valid amount.", "danger")
            return redirect(url_for("cashier.cashier_board"))
        if amount <= 0 or amount > due + 0.01:
            flash(f"Amount must be between 0.01 and {due:.2f}.", "danger")
            return redirect(url_for("cashier.cashier_board"))

    payment = Payment(
        meal_order_id=order.id,
        amount=Decimal(str(round(amount, 2))),
        method=method,
        reference=request.form.get("reference", "").strip()[:80] or None,
        received_by_id=session.get("user_id"),
        tendered=Decimal(str(tendered)) if tendered is not None else None,
        change_given=Decimal(str(change_given)) if change_given is not None else None,
    )
    db.session.add(payment)
    db.session.flush()

    paid = already_paid + amount
    if paid + 0.01 >= float(order.total or 0):
        order.status = "closed"
        order.closed_at = datetime.utcnow()
        if order.table:
            order.table.status = "free"

    db.session.commit()

    if method == "cash":
        flash(
            f"Cash received KES {tendered:.2f}. Change KES {change_given:.2f}.",
            "success",
        )
    else:
        flash("Payment recorded.", "success")

    return redirect(url_for("cashier.receipt", payment_id=payment.id))


@cashier_bp.route("/cashier/receipts/<int:payment_id>")
@login_required(roles={"admin", "cashier"})
def receipt(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    order = payment.meal_order
    paid_total = sum(float(p.amount or 0) for p in order.payments)
    return render_template(
        "receipt.html",
        payment=payment,
        order=order,
        paid_total=paid_total,
    )

