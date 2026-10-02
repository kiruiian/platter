from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from app.extensions import db
from app.models import MenuCategory, MenuItem
from app.routes.auth import login_required


menu_bp = Blueprint("menu", __name__)


@menu_bp.route("/menu")
@login_required()
def menu_list():
    search_query = request.args.get("q", "").strip()
    items_query = MenuItem.query.join(MenuCategory).filter(
        MenuItem.active.is_(True),
        MenuCategory.active.is_(True),
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
    items = (
        items_query
        .order_by(MenuCategory.sort_order, MenuCategory.name, MenuItem.name)
        .limit(10)
        .all()
    )
    item_count = items_query.order_by(None).count()
    category_count = MenuCategory.query.filter_by(active=True).count()
    return render_template(
        "menu.html",
        items=items,
        categories=MenuCategory.query.filter_by(active=True)
        .order_by(MenuCategory.sort_order, MenuCategory.name)
        .all(),
        search_query=search_query,
        item_count=item_count,
        category_count=category_count,
        result_limit=10,
    )


@menu_bp.route("/menu/categories", methods=["POST"])
@login_required(roles={"admin"})
def menu_category_create():
    name = request.form.get("name", "").strip()

    if not name:
        flash("Category name required.", "danger")
        return redirect(url_for("menu.menu_list"))

    if MenuCategory.query.filter_by(name=name).first():
        flash("Category already exists.", "warning")
        return redirect(url_for("menu.menu_list"))

    sort_order = request.form.get("sort_order", type=int) or 0

    db.session.add(
        MenuCategory(
            name=name,
            sort_order=sort_order
        )
    )
    db.session.commit()

    flash(f"Category “{name}” added.", "success")
    return redirect(url_for("menu.menu_list"))


@menu_bp.route("/menu/items", methods=["POST"])
@login_required(roles={"admin"})
def menu_item_create():
    name = request.form.get("name", "").strip()
    category_id = request.form.get("category_id", type=int)

    try:
        price = Decimal(
            request.form.get("price", "0").strip() or "0"
        )
    except Exception:
        flash("Invalid price.", "danger")
        return redirect(url_for("menu.menu_list"))

    if not name or not category_id or price < 0:
        flash("Name, category, and valid price required.", "danger")
        return redirect(url_for("menu.menu_list"))

    if not MenuCategory.query.get(category_id):
        flash("Invalid category.", "danger")
        return redirect(url_for("menu.menu_list"))

    item = MenuItem(
        category_id=category_id,
        name=name,
        description=request.form.get("description", "").strip() or None,
        price=price,
        diet_tags=request.form.get("diet_tags", "").strip() or None,
        is_available=True,
        active=True,
    )

    db.session.add(item)
    db.session.commit()

    flash(f"Item “{name}” added.", "success")
    return redirect(url_for("menu.menu_list"))


@menu_bp.route("/menu/items/<int:item_id>/toggle", methods=["POST"])
@login_required(roles={"admin"})
def menu_item_toggle(item_id):
    item = MenuItem.query.get_or_404(item_id)
    search_query = request.form.get("search_query", "").strip()

    item.is_available = not item.is_available

    db.session.commit()

    flash(
        f"“{item.name}” is now "
        f"{'available' if item.is_available else 'unavailable'}.",
        "success",
    )

    return redirect(url_for("menu.menu_list", q=search_query))