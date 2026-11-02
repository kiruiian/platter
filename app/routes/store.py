from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.models import (
    StockMovement,
    StockPurchase,
    StockPurchaseLine,
    StoreItem,
    Supplier,
)
from app.routes.auth import login_required

store_bp = Blueprint("store", __name__)

def _qty(raw):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value


def _money(raw):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value.quantize(Decimal("0.01"))


def _cost_summary(items):
    """Return weighted purchase cost and estimated value for each store item."""
    rows = (
        db.session.query(
            StockPurchaseLine.item_id,
            db.func.sum(StockPurchaseLine.quantity).label("purchased_quantity"),
            db.func.sum(StockPurchaseLine.line_total).label("purchased_cost"),
        )
        .group_by(StockPurchaseLine.item_id)
        .all()
    )
    costs = {}
    inventory_value = Decimal("0.00")
    for row in rows:
        quantity = Decimal(str(row.purchased_quantity or 0))
        total_cost = Decimal(str(row.purchased_cost or 0))
        if quantity <= 0:
            continue
        costs[row.item_id] = {
            "unit_cost": (total_cost / quantity).quantize(Decimal("0.01"))
        }

    for item in items:
        cost = costs.get(item.id)
        if not cost:
            continue
        estimated_value = (
            Decimal(str(item.quantity_on_hand or 0)) * cost["unit_cost"]
        ).quantize(Decimal("0.01"))
        cost["estimated_value"] = estimated_value
        inventory_value += estimated_value

    return costs, inventory_value

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
    item_costs, inventory_value = _cost_summary(items)
    return render_template(
        "store.html",
        items=items,
        movements=movements,
        low_items=low_items,
        item_costs=item_costs,
        inventory_value=inventory_value,
    )


@store_bp.route("/store/purchases")
@login_required(roles={"admin"})
def purchases_list():
    items = (
        StoreItem.query.filter_by(active=True)
        .order_by(StoreItem.name.asc())
        .all()
    )
    purchases = (
        StockPurchase.query.order_by(
            StockPurchase.purchased_on.desc(),
            StockPurchase.id.desc(),
        )
        .limit(30)
        .all()
    )
    item_costs, inventory_value = _cost_summary(items)
    return render_template(
        "purchases.html",
        items=items,
        suppliers=Supplier.query.filter_by(active=True).order_by(Supplier.name.asc()).all(),
        purchases=purchases,
        item_costs=item_costs,
        inventory_value=inventory_value,
        today=date.today(),
    )


@store_bp.route("/store/suppliers", methods=["POST"])
@login_required(roles={"admin"})
def supplier_create():
    name = request.form.get("name", "").strip()[:120]
    if not name:
        flash("Supplier name is required.", "danger")
        return redirect(url_for("store.purchases_list"))

    if Supplier.query.filter(db.func.lower(Supplier.name) == name.lower()).first():
        flash("That supplier already exists.", "warning")
        return redirect(url_for("store.purchases_list"))

    db.session.add(
        Supplier(
            name=name,
            contact_name=request.form.get("contact_name", "").strip()[:120] or None,
            phone=request.form.get("phone", "").strip()[:40] or None,
            email=request.form.get("email", "").strip()[:120] or None,
        )
    )
    db.session.commit()
    flash(f"Supplier {name} added.", "success")
    return redirect(url_for("store.purchases_list"))


@store_bp.route("/store/purchases", methods=["POST"])
@login_required(roles={"admin"})
def purchase_create():
    raw_date = request.form.get("purchased_on", "").strip()
    try:
        purchased_on = (
            datetime.strptime(raw_date, "%Y-%m-%d").date()
            if raw_date
            else date.today()
        )
    except ValueError:
        flash("Enter a valid purchase date.", "danger")
        return redirect(url_for("store.purchases_list"))

    supplier_id = request.form.get("supplier_id", type=int)
    supplier = Supplier.query.get(supplier_id) if supplier_id else None
    if supplier_id and not supplier:
        flash("Choose a valid supplier.", "danger")
        return redirect(url_for("store.purchases_list"))

    item_ids = request.form.getlist("item_id")
    quantities = request.form.getlist("quantity")
    unit_costs = request.form.getlist("unit_cost")
    if not item_ids or len(item_ids) != len(quantities) or len(item_ids) != len(unit_costs):
        flash("Add at least one complete purchase line.", "danger")
        return redirect(url_for("store.purchases_list"))

    items = {item.id: item for item in StoreItem.query.filter_by(active=True).all()}
    lines = []
    for item_id_raw, quantity_raw, cost_raw in zip(item_ids, quantities, unit_costs):
        try:
            item_id = int(item_id_raw)
        except (TypeError, ValueError):
            flash("Choose a valid store item for every purchase line.", "danger")
            return redirect(url_for("store.purchases_list"))
        item = items.get(item_id)
        quantity = _qty(quantity_raw)
        unit_cost = _money(cost_raw)
        if not item or quantity is None or unit_cost is None:
            flash(
                "Every purchase line needs an item, quantity, and unit cost above zero.",
                "danger",
            )
            return redirect(url_for("store.purchases_list"))
        lines.append((item, quantity, unit_cost))

    purchase = StockPurchase(
        supplier=supplier,
        invoice_number=request.form.get("invoice_number", "").strip()[:120] or None,
        purchased_on=purchased_on,
        notes=request.form.get("notes", "").strip()[:255] or None,
        received_by_id=session.get("user_id"),
    )
    db.session.add(purchase)
    db.session.flush()

    reference = purchase.invoice_number or f"Purchase #{purchase.id}"
    for item, quantity, unit_cost in lines:
        line_total = (quantity * unit_cost).quantize(Decimal("0.01"))
        item.quantity_on_hand = Decimal(str(item.quantity_on_hand or 0)) + quantity
        db.session.add(
            StockPurchaseLine(
                purchase=purchase,
                item=item,
                quantity=quantity,
                unit_cost=unit_cost,
                line_total=line_total,
            )
        )
        db.session.add(
            StockMovement(
                item_id=item.id,
                movement_type="in",
                quantity=quantity,
                reason="purchase",
                reference=reference,
                recorded_by_id=session.get("user_id"),
            )
        )

    db.session.commit()
    flash(f"Purchase {reference} recorded with {len(lines)} item(s).", "success")
    return redirect(url_for("store.purchases_list"))

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
