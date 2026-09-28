from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import RestaurantTable
from app.routes.auth import login_required


tables_bp = Blueprint("tables", __name__)


@tables_bp.route("/tables")
@login_required()
def tables_list():
    tables = (
        RestaurantTable.query.filter_by(active=True)
        .order_by(RestaurantTable.label)
        .all()
    )
    return render_template("tables.html", tables=tables)


@tables_bp.route("/tables", methods=["POST"])
@login_required(roles={"admin"})
def tables_create():
    label = request.form.get("label", "").strip().upper()
    capacity = request.form.get("capacity", type=int) or 4

    if not label:
        flash("Table label required.", "danger")
        return redirect(url_for("tables.tables_list"))

    if RestaurantTable.query.filter_by(label=label).first():
        flash(f"Table {label} already exists.", "warning")
        return redirect(url_for("tables.tables_list"))

    db.session.add(
        RestaurantTable(
            label=label,
            capacity=capacity,
            status="free",
            active=True,
        )
    )
    db.session.commit()

    flash(f"Table {label} added.", "success")
    return redirect(url_for("tables.tables_list"))


@tables_bp.route("/tables/<int:table_id>/status", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def tables_set_status(table_id):
    table = RestaurantTable.query.get_or_404(table_id)

    status = request.form.get("status", "").strip()

    if status not in {"free", "occupied", "reserved"}:
        flash("Invalid status.", "danger")
        return redirect(url_for("tables.tables_list"))

    table.status = status
    db.session.commit()

    flash(f"{table.label} → {status}.", "success")
    return redirect(url_for("tables.tables_list"))