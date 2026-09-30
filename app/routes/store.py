from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import StoreItem, StockMovement
from app.routes.auth import login_required

store_bp = Blueprint("store", __name__)

def _qty(raw):
    try:
        value = Decimal(str(raw).strip())
    except Exception:
        return None
    if value <= 0:
        return None
    return value

@store_bp.route("/store")
@login_required(roles={"admin", "kitchen"})
def store_list():
    items = (
        StoreItem.query.filter_by(active=True)
        .order_by(StoreItem.name.asc())
        .all()
    )
    movements = (
        StockMovement.query.order_by(StockMovement.recorded_at.desc())
        .limit(30)
        .all()
    )
    low_items = [
        i
        for i in items
        if Decimal(str(i.min_qty or 0)) > 0
        and Decimal(str(i.quantity_on_hand or 0)) <= Decimal(str(i.min_qty or 0))
    ]
    return render_template(
        "store.html",
        items=items,
        movements=movements,
        low_items=low_items,
    )

@store_bp.route("/store/items", methods=["POST"])
@login_required(roles={"admin"})
def store_add_item():
    name = request.form.get("name", "").strip()[:120]
    unit = request.form.get("unit", "kg").strip()[:20] or "kg"
    if not name:
        flash("Item name is required.", "danger")
        return redirect(url_for("store.store_list"))
    try:
        min_qty = Decimal(str(request.form.get("min_qty") or "0"))
        if min_qty < 0:
            min_qty = Decimal("0")
    except Exception:
        min_qty = Decimal("0")
    db.session.add(StoreItem(name=name, unit=unit, min_qty=min_qty))
    db.session.commit()
    flash(f"{name} added to store.", "success")
    return redirect(url_for("store.store_list"))

@store_bp.route("/store/<int:item_id>/receive", methods=["POST"])
@login_required(roles={"admin"})
def store_receive(item_id):
    item = StoreItem.query.get_or_404(item_id)
    qty = _qty(request.form.get("quantity"))
    if qty is None:
        flash("Enter a quantity greater than zero.", "danger")
        return redirect(url_for("store.store_list"))
    item.quantity_on_hand = Decimal(str(item.quantity_on_hand or 0)) + qty
    db.session.add(
        StockMovement(
            item_id=item.id,
            movement_type="in",
            quantity=qty,
            reason="purchase",
            reference=request.form.get("reference", "").strip()[:120] or None,
            recorded_by_id=session.get("user_id"),
        )
    )
    db.session.commit()
    flash(f"Received {qty} {item.unit} of {item.name}.", "success")
    return redirect(url_for("store.store_list"))

@store_bp.route("/store/<int:item_id>/issue", methods=["POST"])
@login_required(roles={"admin", "kitchen"})
def store_issue(item_id):
    item = StoreItem.query.get_or_404(item_id)
    qty = _qty(request.form.get("quantity"))
    if qty is None:
        flash("Enter a quantity greater than zero.", "danger")
        return redirect(url_for("store.store_list"))
    on_hand = Decimal(str(item.quantity_on_hand or 0))
    if qty > on_hand:
        flash(f"Only {on_hand} {item.unit} of {item.name} in store.", "danger")
        return redirect(url_for("store.store_list"))
    item.quantity_on_hand = on_hand - qty
    db.session.add(
        StockMovement(
            item_id=item.id,
            movement_type="out",
            quantity=qty,
            reason="kitchen_issue",
            reference=request.form.get("reference", "").strip()[:120] or None,
            recorded_by_id=session.get("user_id"),
        )
    )
    db.session.commit()
    who = session.get("full_name") or session.get("username") or "Staff"
    flash(f"{who} took {qty} {item.unit} {item.name}.", "success")
    return redirect(url_for("store.store_list"))