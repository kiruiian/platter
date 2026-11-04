from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
import base64
import requests

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select, text

from app.extensions import db
from app.models import Expense, HospitalStaff, MealOrder, Payment
from app.routes.auth import login_required

cashier_bp = Blueprint("cashier", __name__)

PAYMENT_METHODS = ("cash", "mpesa", "card", "charge_to_ward")
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value: Decimal | int | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT)


def _parse_money(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _lock_order(order_id: int) -> MealOrder | None:
    if db.session.get_bind().dialect.name == "sqlite":
        db.session.execute(text("BEGIN IMMEDIATE"))
    return db.session.execute(
        select(MealOrder)
        .where(MealOrder.id == order_id)
        .with_for_update()
    ).scalar_one_or_none()


def _has_pending_payment(order_id: int) -> bool:
    return Payment.query.filter_by(
        meal_order_id=order_id,
        result_desc="pending",
    ).first() is not None


def _confirmed_paid(order_id: int) -> Decimal:
    payments = Payment.query.filter(
        Payment.meal_order_id == order_id,
        db.or_(Payment.result_desc.is_(None), Payment.result_desc != "pending"),
    ).all()
    return sum((_money(payment.amount) for payment in payments), ZERO)


def _close_order(order: MealOrder) -> None:
    order.status = "closed"
    order.closed_at = datetime.utcnow()
    if order.table:
        still_open = MealOrder.query.filter(
            MealOrder.table_id == order.table_id,
            MealOrder.id != order.id,
            MealOrder.status.in_(
                ["draft", "submitted", "in_kitchen", "ready", "delivered"]
            ),
        ).first()
        order.table.status = "occupied" if still_open else "free"


@cashier_bp.route("/cashier")
@login_required(roles={"admin", "cashier"})
def cashier_board():
    raw = (
    MealOrder.query.filter(
        MealOrder.status.in_(["ready", "delivered"]),
        MealOrder.party_type == "customer",
    )
    .order_by(MealOrder.opened_at.asc())
    .all()
)
    orders = []
    for order in raw:
        paid = sum(
        (
            _money(payment.amount)
            for payment in order.payments
            if (payment.result_desc or "") != "pending"
        ),
        ZERO,
    )
    if paid < _money(order.total):
        orders.append(order)

    return render_template(
        "cashier.html",
        orders=orders,
        mpesa_till=os.environ.get("MPESA_TILL", ""),
    )


@cashier_bp.route("/cashier/<int:order_id>/pay", methods=["POST"])
@login_required(roles={"admin", "cashier"})
def cashier_pay(order_id):
    method = request.form.get("method", "").strip()
    if method not in PAYMENT_METHODS:
        flash("Choose a valid payment method.", "danger")
        return redirect(url_for("cashier.cashier_board"))

    reference = request.form.get("reference", "").strip()[:80] or None
    if method == "mpesa" and not reference:
        flash("Enter the M-Pesa confirmation code.", "danger")
        return redirect(url_for("cashier.cashier_board"))

    raw_tendered = request.form.get("tendered", "").strip()
    tendered = _parse_money(raw_tendered) if method == "cash" else None
    if method == "cash" and tendered is None:
        flash("Enter a valid cash amount.", "danger")
        return redirect(url_for("cashier.cashier_board"))

    raw_amount = request.form.get("amount", "").strip()
    requested_amount = (
        _parse_money(raw_amount) if method != "cash" and raw_amount else None
    )
    if method != "cash" and raw_amount and requested_amount is None:
        flash("Enter a valid amount.", "danger")
        return redirect(url_for("cashier.cashier_board"))

    payment_id = None
    with db.session.begin():
        order = _lock_order(order_id)
        if not order:
            abort(404)
        if order.status not in {"ready", "delivered"}:
            flash("This order is not ready for payment.", "warning")
            return redirect(url_for("cashier.cashier_board"))
        if _has_pending_payment(order.id):
            flash("An M-Pesa prompt is pending for this order.", "warning")
            return redirect(url_for("cashier.cashier_board"))

        due = _money(order.total) - _confirmed_paid(order.id)
        if due <= ZERO:
            flash("Order is already fully paid.", "warning")
            return redirect(url_for("cashier.cashier_board"))

        amount = due if method == "cash" or requested_amount is None else requested_amount
        change_given = None
        if method == "cash":
            if tendered < due:
                flash(f"Cash received is less than balance KES {due:.2f}.", "danger")
                return redirect(url_for("cashier.cashier_board"))
            amount = due
            change_given = tendered - due
        elif amount <= ZERO or amount > due:
            flash(f"Amount must be between 0.01 and {due:.2f}.", "danger")
            return redirect(url_for("cashier.cashier_board"))

        if method == "mpesa":
            existing = Payment.query.filter(
                Payment.method == "mpesa",
                db.func.lower(Payment.reference) == reference.lower(),
                db.or_(Payment.result_desc.is_(None), Payment.result_desc != "pending"),
            ).first()
            if existing:
                flash(f"M-Pesa code {reference} is already recorded.", "danger")
                return redirect(url_for("cashier.cashier_board"))

        payment = Payment(
            meal_order_id=order.id,
            amount=amount,
            method=method,
            reference=reference,
            received_by_id=session.get("user_id"),
            tendered=tendered,
            change_given=change_given,
        )
        db.session.add(payment)
        db.session.flush()
        payment_id = payment.id

        if _confirmed_paid(order.id) >= _money(order.total):
            _close_order(order)

    if method == "cash":
        flash(
            f"Cash received KES {tendered:.2f}. Change KES {change_given:.2f}.",
            "success",
        )
    else:
        flash("Payment recorded.", "success")

    return redirect(url_for("cashier.receipt", payment_id=payment_id))

@cashier_bp.route("/cashier/receipts/<int:payment_id>")
@login_required(roles={"admin", "cashier"})
def receipt(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    order = payment.meal_order
    paid_total = sum((_money(p.amount) for p in order.payments), ZERO)
    return render_template(
        "receipt.html",
        payment=payment,
        order=order,
        paid_total=paid_total,
    )

def _mpesa_base():
    if os.environ.get("MPESA_ENV", "sandbox") == "production":
        return "https://api.safaricom.co.ke"
    return "https://sandbox.safaricom.co.ke"

def _mpesa_token():
    key = os.environ.get("MPESA_CONSUMER_KEY", "")
    secret = os.environ.get("MPESA_CONSUMER_SECRET", "")
    r = requests.get(
        _mpesa_base() + "/oauth/v1/generate?grant_type=client_credentials",
        auth=(key, secret),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def _norm_phone(raw: str):
    s = "".join(ch for ch in (raw or "") if ch.isdigit())
    if s.startswith("0") and len(s) == 10:
        s = "254" + s[1:]
    if s.startswith("254") and len(s) == 12:
        return s
    return None

@cashier_bp.route("/cashier/<int:order_id>/stk", methods=["POST"])
@login_required(roles={"admin", "cashier"})
def stk_push(order_id):
    phone = _norm_phone(request.form.get("phone", ""))
    if not phone:
        flash("Enter a valid Kenyan number (07… or 2547…).", "danger")
        return redirect(url_for("cashier.cashier_board"))

    with db.session.begin():
        order = _lock_order(order_id)
        if not order:
            abort(404)
        if order.status not in {"ready", "delivered"}:
            flash("Order is not ready for payment.", "warning")
            return redirect(url_for("cashier.cashier_board"))
        if _has_pending_payment(order.id):
            flash("An M-Pesa prompt is already pending for this order.", "warning")
            return redirect(url_for("cashier.cashier_board"))

        due = _money(order.total) - _confirmed_paid(order.id)
        if due <= ZERO:
            flash("Order already paid.", "warning")
            return redirect(url_for("cashier.cashier_board"))
        if due != due.to_integral_value():
            flash(
                "M-Pesa prompts require a whole-shilling balance. "
                "Record the till payment instead.",
                "warning",
            )
            return redirect(url_for("cashier.cashier_board"))

        shortcode = os.environ.get("MPESA_SHORTCODE", "")
        passkey = os.environ.get("MPESA_PASSKEY", "")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{shortcode}{passkey}{timestamp}".encode()
        ).decode()
        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(due),
            "PartyA": phone,
            "PartyB": shortcode,
            "PhoneNumber": phone,
            "CallBackURL": os.environ.get("MPESA_CALLBACK_URL"),
            "AccountReference": (order.order_number or "PLATTER")[:12],
            "TransactionDesc": (order.order_number or "Platter")[:13],
        }
        pending = Payment(
            meal_order_id=order.id,
            amount=due,
            method="mpesa_stk",
            phone=phone,
            result_desc="pending",
            received_by_id=session.get("user_id"),
        )
        db.session.add(pending)
        db.session.flush()
        pending_id = pending.id

    try:
        token = _mpesa_token()
        resp = requests.post(
            _mpesa_base() + "/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        data = resp.json()
    except Exception:
        current_app.logger.exception("M-Pesa STK request status is unknown.")
        flash(
            "M-Pesa status is unclear. Verify the prompt before recording "
            "another payment.",
            "danger",
        )
        return redirect(url_for("cashier.cashier_board"))

    if str(data.get("ResponseCode")) != "0":
        with db.session.begin():
            pending = db.session.get(Payment, pending_id)
            if pending and pending.result_desc == "pending":
                db.session.delete(pending)
        flash(
            data.get("errorMessage")
            or data.get("ResponseDescription")
            or "STK failed",
            "danger",
        )
        return redirect(url_for("cashier.cashier_board"))

    with db.session.begin():
        pending = db.session.get(Payment, pending_id)
        if pending:
            pending.checkout_request_id = data.get("CheckoutRequestID")
    flash("Prompt sent. Ask the guest to enter PIN.", "success")
    return redirect(url_for("cashier.cashier_board"))

@cashier_bp.route("/mpesa/stk-callback", methods=["POST"])
def stk_callback():
    body = request.get_json(silent=True) or {}
    stk = (body.get("Body") or {}).get("stkCallback") or {}
    checkout_id = stk.get("CheckoutRequestID")
    result_code = stk.get("ResultCode")
    result_desc = stk.get("ResultDesc")

    with db.session.begin():
        payment_ref = Payment.query.filter_by(
            checkout_request_id=checkout_id
        ).first()
        if not payment_ref:
            return {"ok": True}

        order = _lock_order(payment_ref.meal_order_id)
        payment = db.session.execute(
            select(Payment)
            .where(Payment.id == payment_ref.id)
            .with_for_update()
        ).scalar_one_or_none()
        if not order or not payment or payment.result_desc != "pending":
            return {"ok": True}

        if result_code == 0:
            items = ((stk.get("CallbackMetadata") or {}).get("Item") or [])
            meta = {item.get("Name"): item.get("Value") for item in items}
            reported_amount = _parse_money(str(meta.get("Amount", "")))
            if reported_amount != _money(payment.amount):
                current_app.logger.error(
                    "M-Pesa callback amount does not match pending payment %s.",
                    payment.id,
                )
                return {"ok": True}

            payment.result_desc = (result_desc or "")[:255]
            payment.mpesa_receipt = str(meta.get("MpesaReceiptNumber") or "")[:40]
            payment.reference = payment.mpesa_receipt
            if _confirmed_paid(order.id) >= _money(order.total):
                _close_order(order)
        else:
            db.session.delete(payment)

    return {"ok": True}

@cashier_bp.route("/reports/daily")
@login_required(roles={"admin", "cashier"})
def daily_report():
    raw = request.args.get("date", "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now().date()

    # Wider window to catch timezone differences (EAT vs UTC)
    start = datetime(day.year, day.month, day.day) - timedelta(hours=3)
    end = datetime(day.year, day.month, day.day) + timedelta(days=1, hours=3)

    payments = (
        Payment.query.filter(
            Payment.received_at >= start,
            Payment.received_at < end,
        )
        .order_by(Payment.received_at.desc())
        .all()
    )
    confirmed = [p for p in payments if (p.result_desc or "") != "pending"]

    by_method = defaultdict(lambda: {"count": 0, "total": ZERO})
    for p in confirmed:
        key = p.method or "other"
        by_method[key]["count"] += 1
        by_method[key]["total"] += _money(p.amount)

    grand = sum((v["total"] for v in by_method.values()), ZERO)
    closed = MealOrder.query.filter(
        MealOrder.status == "closed",
        MealOrder.closed_at >= start,
        MealOrder.closed_at < end,
    ).count()

    day_expenses = Expense.query.filter(
        Expense.expense_date == day
    ).all()
    expenses_total = sum((_money(e.amount) for e in day_expenses), ZERO)
    net_profit = grand - expenses_total

    return render_template(
        "reports_daily.html",
        start=start,
        day=day,
        by_method=dict(by_method),
        grand=grand,
        closed=closed,
        payments=confirmed,
        expenses_total=expenses_total,
        net_profit=net_profit,
    )

@cashier_bp.route("/reports/monthly")
@login_required(roles={"admin", "cashier"})
def monthly_report():
    raw = request.args.get("month", "").strip()
    try:
        month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        today = datetime.now().date()
        month = today.replace(day=1)

    start = datetime.combine(month, datetime.min.time())
    next_month = month.month % 12 + 1
    next_year = month.year + (month.month // 12)
    end = datetime(next_year, next_month, 1)

    payments = (
        Payment.query.filter(
            Payment.received_at >= start,
            Payment.received_at < end,
        )
        .order_by(Payment.received_at.desc())
        .all()
    )
    confirmed = [p for p in payments if (p.result_desc or "") != "pending"]

    by_method = defaultdict(lambda: {"count": 0, "total": ZERO})
    for payment in confirmed:
        key = payment.method or "other"
        by_method[key]["count"] += 1
        by_method[key]["total"] += _money(payment.amount)

    grand = sum((row["total"] for row in by_method.values()), ZERO)
    closed = MealOrder.query.filter(
        MealOrder.status == "closed",
        MealOrder.closed_at >= start,
        MealOrder.closed_at < end,
    ).count()
    # Expenses for the month
    month_expenses = Expense.query.filter(
    Expense.expense_date >= month,
    Expense.expense_date < end.date() if hasattr(end, 'date') else end
    ).all()
    expenses_total = sum((_money(e.amount) for e in month_expenses), ZERO)
    net_profit = grand - expenses_total

    return render_template(
        "reports_monthly.html",
        month=month,
        by_method=dict(by_method),
        grand=grand,
        closed=closed,
        payments=confirmed,
        expenses_total=expenses_total,
        net_profit=net_profit,
    )

@cashier_bp.route("/reports/staff")
@login_required(roles={"admin", "cashier"})
def staff_meals_report():
    raw = request.args.get("month", "").strip()
    staff_filter = request.args.get("staff_id", type=int)

    try:
        month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        today = datetime.now().date()
        month = today.replace(day=1)

    start = datetime.combine(month, datetime.min.time())
    next_month = month.month % 12 + 1
    next_year = month.year + (month.month // 12)
    end = datetime(next_year, next_month, 1)

    query = MealOrder.query.filter(
        MealOrder.party_type == "staff",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    )

    if staff_filter:
        query = query.filter(MealOrder.staff_id == staff_filter)

    orders = query.order_by(MealOrder.opened_at.desc()).all()

    from collections import defaultdict
    by_staff = defaultdict(lambda: {"count": 0, "total": ZERO, "name": "Unknown"})
    for order in orders:
        key = order.staff_id or 0
        by_staff[key]["count"] += 1
        by_staff[key]["total"] += _money(order.total)
        if order.staff:
            by_staff[key]["name"] = order.staff.full_name

    all_staff = HospitalStaff.query.filter_by(active=True).order_by(HospitalStaff.full_name).all()

    return render_template(
        "reports_staff.html",
        month=month,
        by_staff=dict(by_staff),
        orders=orders,
        grand=sum((v["total"] for v in by_staff.values()), ZERO),
        all_staff=all_staff,
        selected_staff=staff_filter,
    )
@cashier_bp.route("/reports/staff/export")
@login_required(roles={"admin", "cashier"})
def staff_meals_export():
    raw = request.args.get("month", "").strip()
    staff_filter = request.args.get("staff_id", type=int)

    try:
        month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        today = datetime.now().date()
        month = today.replace(day=1)

    start = datetime.combine(month, datetime.min.time())
    next_month = month.month % 12 + 1
    next_year = month.year + (month.month // 12)
    end = datetime(next_year, next_month, 1)

    query = MealOrder.query.filter(
        MealOrder.party_type == "staff",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    )
    if staff_filter:
        query = query.filter(MealOrder.staff_id == staff_filter)

    orders = query.order_by(MealOrder.opened_at.desc()).all()

    import csv
    from io import StringIO
    from flask import Response

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date", "Order", "Staff", "Items", "Total"])

    for order in orders:
        items = "; ".join(
            f"{line.quantity}x {line.item_name}"
            for line in order.lines if line.status != "void"
        )
        writer.writerow([
            order.opened_at.strftime("%Y-%m-%d %H:%M") if order.opened_at else "",
            order.order_number,
            order.staff.full_name if order.staff else "",
            items,
            _money(order.total),
        ])

    output = si.getvalue()
    filename = f"staff_meals_{month.strftime('%Y-%m')}.csv"
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@cashier_bp.route("/reports/patients")
@login_required(roles={"admin", "cashier"})
def patient_meals_report():
    raw = request.args.get("month", "").strip()
    ward_filter = request.args.get("ward", "").strip()

    try:
        month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        today = datetime.now().date()
        month = today.replace(day=1)

    start = datetime.combine(month, datetime.min.time())
    next_month = month.month % 12 + 1
    next_year = month.year + (month.month // 12)
    end = datetime(next_year, next_month, 1)

    query = MealOrder.query.filter(
        MealOrder.party_type == "patient",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    )

    if ward_filter:
        query = query.filter(MealOrder.ward_name.ilike(f"%{ward_filter}%"))

    orders = query.order_by(MealOrder.opened_at.desc()).all()

    from collections import defaultdict
    by_ward = defaultdict(lambda: {"count": 0, "total": ZERO})
    for order in orders:
        key = (order.ward_name or "Unknown").strip() or "Unknown"
        by_ward[key]["count"] += 1
        by_ward[key]["total"] += _money(order.total)

    return render_template(
        "reports_patients.html",
        month=month,
        by_ward=dict(by_ward),
        orders=orders,
        grand=sum((v["total"] for v in by_ward.values()), ZERO),
        ward_filter=ward_filter,
    )

@cashier_bp.route("/reports/patients/export")
@login_required(roles={"admin", "cashier"})
def patient_meals_export():
    raw = request.args.get("month", "").strip()
    ward_filter = request.args.get("ward", "").strip()

    try:
        month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        today = datetime.now().date()
        month = today.replace(day=1)

    start = datetime.combine(month, datetime.min.time())
    next_month = month.month % 12 + 1
    next_year = month.year + (month.month // 12)
    end = datetime(next_year, next_month, 1)

    query = MealOrder.query.filter(
        MealOrder.party_type == "patient",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    )
    if ward_filter:
        query = query.filter(MealOrder.ward_name.ilike(f"%{ward_filter}%"))

    orders = query.order_by(MealOrder.opened_at.desc()).all()

    import csv
    from io import StringIO
    from flask import Response

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date", "Order", "Ward", "Patient/Bed", "Items", "Total"])

    for order in orders:
        items = "; ".join(
            f"{line.quantity}x {line.item_name}"
            for line in order.lines if line.status != "void"
        )
        writer.writerow([
            order.opened_at.strftime("%Y-%m-%d %H:%M") if order.opened_at else "",
            order.order_number,
            order.ward_name or "",
            order.patient_name or "",
            items,
            _money(order.total),
        ])

    output = si.getvalue()
    filename = f"patient_meals_{month.strftime('%Y-%m')}.csv"
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@cashier_bp.route("/reports/daily-summary")
@login_required(roles={"admin", "cashier"})
def daily_summary():
    raw = request.args.get("date", "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now().date()

    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)

    payments = Payment.query.filter(
        Payment.received_at >= start,
        Payment.received_at < end,
    ).all()
    confirmed = [p for p in payments if (p.result_desc or "") != "pending"]
    sales = sum((_money(p.amount) for p in confirmed), ZERO)

    expenses = Expense.query.filter(Expense.expense_date == day).all()
    expenses_total = sum((_money(e.amount) for e in expenses), ZERO)

    staff_orders = MealOrder.query.filter(
        MealOrder.party_type == "staff",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    ).all()
    staff_total = sum((_money(order.total) for order in staff_orders), ZERO)

    patient_orders = MealOrder.query.filter(
        MealOrder.party_type == "patient",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    ).all()
    patient_total = sum((_money(order.total) for order in patient_orders), ZERO)

    closed = MealOrder.query.filter(
        MealOrder.status == "closed",
        MealOrder.closed_at >= start,
        MealOrder.closed_at < end,
    ).count()

    return render_template(
        "daily_summary.html",
        day=day,
        sales=sales,
        expenses_total=expenses_total,
        net_profit=sales - expenses_total,
        staff_total=staff_total,
        patient_total=patient_total,
        closed=closed,
        expenses=expenses,
    )


@cashier_bp.route("/reports/monthly-summary")
@login_required(roles={"admin", "cashier"})
def monthly_summary():
    raw = request.args.get("month", "").strip()
    try:
        month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        today = datetime.now().date()
        month = today.replace(day=1)

    start = datetime(month.year, month.month, 1)
    next_month = month.month % 12 + 1
    next_year = month.year + (month.month // 12)
    end = datetime(next_year, next_month, 1)

    payments = Payment.query.filter(
        Payment.received_at >= start,
        Payment.received_at < end,
    ).all()
    confirmed = [p for p in payments if (p.result_desc or "") != "pending"]
    sales = sum((_money(p.amount) for p in confirmed), ZERO)

    expenses = Expense.query.filter(
        Expense.expense_date >= month,
        Expense.expense_date < end.date(),
    ).all()
    expenses_total = sum((_money(e.amount) for e in expenses), ZERO)

    staff_orders = MealOrder.query.filter(
        MealOrder.party_type == "staff",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    ).all()
    staff_total = sum((_money(order.total) for order in staff_orders), ZERO)

    patient_orders = MealOrder.query.filter(
        MealOrder.party_type == "patient",
        MealOrder.opened_at >= start,
        MealOrder.opened_at < end,
        MealOrder.status != "cancelled",
    ).all()
    patient_total = sum((_money(order.total) for order in patient_orders), ZERO)

    closed = MealOrder.query.filter(
        MealOrder.status == "closed",
        MealOrder.closed_at >= start,
        MealOrder.closed_at < end,
    ).count()

    return render_template(
        "monthly_summary.html",
        month=month,
        sales=sales,
        expenses_total=expenses_total,
        net_profit=sales - expenses_total,
        staff_total=staff_total,
        patient_total=patient_total,
        closed=closed,
        expenses=expenses,
    )

@cashier_bp.route("/reports/daily-summary/export")
@login_required(roles={"admin", "cashier"})
def daily_summary_export():
    raw = request.args.get("date", "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now().date()

    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)

    payments = Payment.query.filter(
        Payment.received_at >= start,
        Payment.received_at < end,
    ).all()
    confirmed = [p for p in payments if (p.result_desc or "") != "pending"]
    sales = sum((_money(p.amount) for p in confirmed), ZERO)

    expenses = Expense.query.filter(Expense.expense_date == day).all()
    expenses_total = sum((_money(e.amount) for e in expenses), ZERO)

    import csv
    from io import StringIO
    from flask import Response

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Daily Summary", day.strftime("%Y-%m-%d")])
    writer.writerow([])
    writer.writerow(["Sales", sales])
    writer.writerow(["Expenses", expenses_total])
    writer.writerow(["Net Profit", sales - expenses_total])
    writer.writerow([])
    writer.writerow(["Expenses Detail"])
    writer.writerow(["Description", "Category", "Amount"])
    for e in expenses:
        writer.writerow([e.description, e.category, _money(e.amount)])

    output = si.getvalue()
    filename = f"daily_summary_{day.strftime('%Y-%m-%d')}.csv"
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@cashier_bp.route("/reports/monthly-summary/export")
@login_required(roles={"admin", "cashier"})
def monthly_summary_export():
    raw = request.args.get("month", "").strip()
    try:
        month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        today = datetime.now().date()
        month = today.replace(day=1)

    start = datetime(month.year, month.month, 1)
    next_month = month.month % 12 + 1
    next_year = month.year + (month.month // 12)
    end = datetime(next_year, next_month, 1)

    payments = Payment.query.filter(
        Payment.received_at >= start,
        Payment.received_at < end,
    ).all()
    confirmed = [p for p in payments if (p.result_desc or "") != "pending"]
    sales = sum((_money(p.amount) for p in confirmed), ZERO)

    expenses = Expense.query.filter(
        Expense.expense_date >= month,
        Expense.expense_date < end.date(),
    ).all()
    expenses_total = sum((_money(e.amount) for e in expenses), ZERO)

    import csv
    from io import StringIO
    from flask import Response

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Monthly Summary", month.strftime("%Y-%m")])
    writer.writerow([])
    writer.writerow(["Sales", sales])
    writer.writerow(["Expenses", expenses_total])
    writer.writerow(["Net Profit", sales - expenses_total])
    writer.writerow([])
    writer.writerow(["Expenses Detail"])
    writer.writerow(["Date", "Description", "Category", "Amount"])
    for e in expenses:
        writer.writerow([e.expense_date, e.description, e.category, _money(e.amount)])

    output = si.getvalue()
    filename = f"monthly_summary_{month.strftime('%Y-%m')}.csv"
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )