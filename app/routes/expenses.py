from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from app.extensions import db
from app.models import Expense
from app.routes.auth import login_required

expenses_bp = Blueprint("expenses", __name__)

@expenses_bp.route("/expenses")
@login_required(roles={"admin", "cashier"})
def expenses_list():
    expenses = Expense.query.order_by(Expense.expense_date.desc()).limit(50).all()
    return render_template("expenses.html", expenses=expenses)

@expenses_bp.route("/expenses", methods=["POST"])
@login_required(roles={"admin", "cashier"})
def expenses_create():
    description = request.form.get("description", "").strip()
    amount = request.form.get("amount", type=float)
    category = request.form.get("category", "other")
    raw_date = request.form.get("expense_date", "").strip()

    if not description or not amount or amount <= 0:
        flash("Description and a valid amount are required.", "danger")
        return redirect(url_for("expenses.expenses_list"))

    try:
        expense_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else datetime.utcnow().date()
    except ValueError:
        expense_date = datetime.utcnow().date()

    db.session.add(Expense(
        expense_date=expense_date,
        description=description,
        amount=amount,
        category=category,
        created_by_id=session.get("user_id"),
    ))
    db.session.commit()
    flash("Expense recorded.", "success")
    return redirect(url_for("expenses.expenses_list"))