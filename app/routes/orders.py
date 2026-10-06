from decimal import Decimal
from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import or_

from app.extensions import db
from app.models import (
    MealOrder,
    MealOrderLine,
    MenuCategory,
    MenuItem,
    RestaurantTable,
)
from app.routes.auth import login_required


orders_bp = Blueprint("orders", __name__)


def next_order_number() -> str:
    today = datetime.utcnow().strftime("%Y%m%d")
    prefix = f"PL-{today}-"

    last = (
        MealOrder.query
        .filter(MealOrder.order_number.like(f"{prefix}%"))
        .order_by(MealOrder.id.desc())
        .first()
    )

    seq = 1

    if last and last.order_number:
        try:
            seq = int(last.order_number.split("-")[-1]) + 1
        except ValueError:
            seq = 1

    return f"{prefix}{seq:04d}"


def recalculate_order_totals(order: MealOrder) -> None:
    sub = sum(
        (line.line_total or 0)
        for line in order.lines
        if line.status != "void"
    )

    order.subtotal = sub
    order.tax = Decimal("0.00")
    order.total = (order.subtotal or 0) + (order.tax or 0)

@orders_bp.route("/orders")
@login_required()
def orders_list():
    search_query = request.args.get("q", "").strip()
    orders_query = MealOrder.query.filter(
        MealOrder.status.in_(
            ["draft", "submitted", "in_kitchen", "ready", "delivered"]
        )
    )
    if search_query:
        pattern = f"%{search_query}%"
        orders_query = orders_query.outerjoin(RestaurantTable).filter(
            or_(
                MealOrder.order_number.ilike(pattern),
                MealOrder.customer_name.ilike(pattern),
                RestaurantTable.label.ilike(pattern),
            )
        )
    orders = (
        orders_query.order_by(MealOrder.opened_at.desc())
        .limit(10)
        .all()
    )
    order_count = orders_query.order_by(None).count()
    ready_count = MealOrder.query.filter_by(status="ready").count()
    return render_template(
        "orders.html",
        orders=orders,
        ready_count=ready_count,
        order_count=order_count,
        search_query=search_query,
        result_limit=10,
    )

@orders_bp.route("/orders/open/<int:table_id>", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_open_table(table_id):
    table = RestaurantTable.query.get_or_404(table_id)
    if not table.active:
        flash("Table is inactive.", "danger")
        return redirect(url_for("tables.tables_list"))

    party_type = request.form.get("party_type", "customer").strip()
    if party_type not in {"customer", "staff", "patient"}:
        party_type = "customer"

    order = MealOrder(
        order_number=next_order_number(),
        order_type="dine_in",
        status="draft",
        table_id=table.id,
        opened_by_id=session.get("user_id"),
        party_type=party_type,
        customer_name=request.form.get("customer_name", "").strip()[:120] or None,
    )
    table.status = "occupied"
    db.session.add(order)
    db.session.commit()
    flash(f"Order {order.order_number} opened on {table.label}.", "success")
    return redirect(url_for("orders.order_detail", order_id=order.id))

@orders_bp.route("/orders/<int:order_id>/add-item")
@login_required()
def order_detail(order_id):
    order = MealOrder.query.get_or_404(order_id)
    search_query = request.args.get("q", "").strip()
    items_query = MenuItem.query.join(MenuCategory).filter(
        MenuCategory.active.is_(True),
        MenuItem.active.is_(True),
        MenuItem.is_available.is_(True),
    )
    if search_query:
        pattern = f"%{search_query}%"
        items_query = items_query.filter(
            or_(
                MenuItem.name.ilike(pattern),
                MenuItem.description.ilike(pattern),
                MenuItem.diet_tags.ilike(pattern),
                MenuCategory.name.ilike(pattern),
            )
        )
    menu_items = (
        items_query
        .order_by(MenuCategory.sort_order, MenuCategory.name, MenuItem.name)
        .limit(10)
        .all()
    )
    item_count = items_query.order_by(None).count()
    return render_template(
        "order_detail.html",
        order=order,
        menu_items=menu_items,
        categories=MenuCategory.query.filter_by(active=True)
        .order_by(MenuCategory.sort_order, MenuCategory.name)
        .all(),
        search_query=search_query,
        item_count=item_count,
        result_limit=10,
    )

@orders_bp.route("/orders/<int:order_id>/add-item", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_add_item(order_id):
    order = MealOrder.query.get_or_404(order_id)
    if order.status in {"closed", "cancelled"}:
        flash("This order is closed.", "warning")
        search_query = request.form.get("search_query", "").strip()
        return redirect(url_for("orders.order_detail", order_id=order.id, q=search_query))

    item_id = request.form.get("menu_item_id", type=int)
    qty = request.form.get("quantity", type=int) or 1
    if qty < 1:
        qty = 1
    item = MenuItem.query.get(item_id)
    if not item or not item.active or not item.is_available:
        flash("Item not available.", "danger")
        search_query = request.form.get("search_query", "").strip()
        return redirect(url_for("orders.order_detail", order_id=order.id, q=search_query))

    line = MealOrderLine(
        meal_order_id=order.id,
        menu_item_id=item.id,
        item_name=item.name,
        unit_price=item.price,
        quantity=qty,
        line_total=item.price * qty,
        status="queued",
        notes=request.form.get("notes", "").strip()[:255] or None,
    )
    db.session.add(line)
    db.session.flush()
    recalculate_order_totals(order)
    db.session.commit()
    flash(f"Added {qty} × {item.name}.", "success")
    search_query = request.form.get("search_query", "").strip()
    return redirect(url_for("orders.order_detail", order_id=order.id, q=search_query))

@orders_bp.route("/orders/<int:order_id>/submit", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_submit(order_id):
    order = MealOrder.query.get_or_404(order_id)
    active_lines = [ln for ln in order.lines if ln.status != "void"]
    if not active_lines:
        flash("Add at least one item before sending to kitchen.", "danger")
        return redirect(url_for("orders.order_detail", order_id=order.id))
    if order.status not in {"draft", "submitted"}:
        flash("Order cannot be submitted in its current status.", "warning")
        return redirect(url_for("orders.order_detail", order_id=order.id))
    order.status = "submitted"
    db.session.commit()
    flash(f"{order.order_number} sent to kitchen.", "success")
    return redirect(url_for("orders.order_detail", order_id=order.id))

@orders_bp.route("/orders/<int:order_id>/lines/<int:line_id>/void", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_void_line(order_id, line_id):
    order = MealOrder.query.get_or_404(order_id)
    line = MealOrderLine.query.filter_by(
        id=line_id, meal_order_id=order.id
    ).first_or_404()
    line.status = "void"
    recalculate_order_totals(order)
    db.session.commit()
    flash(f"Voided {line.item_name}.", "success")
    return redirect(url_for("orders.order_detail", order_id=order.id))


@orders_bp.route("/orders/<int:order_id>/ready", methods=["POST"])
@login_required(roles={"admin", "waiter", "cashier"})
def order_mark_ready(order_id):
    order = MealOrder.query.get_or_404(order_id)
    if order.status not in {"submitted", "in_kitchen"}:
        flash("Only a sent ticket can be marked ready.", "warning")
        return redirect(url_for("orders.order_detail", order_id=order.id))
    order.status = "ready"
    db.session.commit()
    flash(f"{order.order_number} is ready for the cashier.", "success")
    return redirect(url_for("orders.order_detail", order_id=order.id))



@orders_bp.route("/orders/<int:order_id>/served", methods=["POST"])
@login_required(roles={"admin", "waiter"})
def order_mark_served(order_id):
    order = MealOrder.query.get_or_404(order_id)
    if order.status != "ready":
        flash("Only ready orders can be marked served.", "warning")
        return redirect(url_for("orders.order_detail", order_id=order.id))
    order.status = "delivered"
    db.session.commit()
    flash(f"{order.order_number} marked served. Cashier can bill.", "success")
    return redirect(url_for("orders.order_detail", order_id=order.id))