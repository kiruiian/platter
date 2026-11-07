import requests
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import MealOrder, RestaurantTable, HospitalStaff
from app.routes.auth import login_required


tables_bp = Blueprint("tables", __name__)

OPEN_STATUSES = ("draft", "submitted", "in_kitchen", "ready", "delivered")


def load_hmis_wards():
    base_url = current_app.config.get("HMIS_API_BASE_URL", "").rstrip("/")
    token = current_app.config.get("HMIS_API_TOKEN", "")
    if not base_url or not token:
        return None, "HMIS ward lookup is not configured; enter the ward manually."
    if len(token) < 32 or not token.isascii():
        current_app.logger.error(
            "HMIS integration token must be at least 32 ASCII characters."
        )
        return None, "HMIS integration token must be at least 32 characters."
    if (
        current_app.config.get("ENVIRONMENT") == "production"
        and not base_url.lower().startswith("https://")
    ):
        current_app.logger.error("HMIS ward lookup must use HTTPS in production.")
        return None, "HMIS ward lookup must use HTTPS in production."

    try:
        response = requests.get(
            f"{base_url}/api/v1/integrations/platter/wards",
            headers={"Authorization": f"Bearer {token}"},
            timeout=(2, 4),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        current_app.logger.warning("HMIS ward lookup failed: %s", exc)
        return None, "HMIS ward lookup is unavailable; enter the ward manually."

    try:
        payload = response.json()
    except ValueError:
        current_app.logger.warning("HMIS ward lookup returned invalid JSON.")
        return None, "HMIS ward lookup returned an invalid response."

    wards = payload.get("wards") if isinstance(payload, dict) else None
    if not isinstance(wards, list) or any(
        not isinstance(ward, dict)
        or not isinstance(ward.get("name"), str)
        or not ward["name"].strip()
        or (
            ward.get("department") is not None
            and not isinstance(ward.get("department"), str)
        )
        for ward in wards
    ):
        current_app.logger.warning("HMIS ward lookup returned an invalid ward list.")
        return None, "HMIS ward lookup returned an invalid response."

    return wards, None


@tables_bp.route("/tables")
@login_required()
def tables_list():
    tables = (
        RestaurantTable.query.filter_by(active=True)
        .order_by(RestaurantTable.label)
        .all()
    )
    open_orders = (
        MealOrder.query.filter(MealOrder.status.in_(OPEN_STATUSES))
        .order_by(MealOrder.opened_at.desc())
        .all()
    )
    current = {}
    for order in open_orders:
        if order.table_id and order.table_id not in current:
            current[order.table_id] = order
    staff = HospitalStaff.query.filter_by(active=True).order_by(HospitalStaff.full_name).all()
    hmis_wards, hmis_ward_message = load_hmis_wards()
    return render_template(
        "tables.html",
        tables=tables,
        current=current,
        staff=staff,
        hmis_wards=hmis_wards,
        hmis_ward_message=hmis_ward_message,
    )


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