from datetime import datetime
from decimal import Decimal
import os
import base64
import requests

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
    return render_template("cashier.html", orders=orders, mpesa_till=os.environ.get("MPESA_TILL", ""))


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
    order = MealOrder.query.get_or_404(order_id)
    if order.status not in {"ready", "delivered"}:
        flash("Order is not ready for payment.", "warning")
        return redirect(url_for("cashier.cashier_board"))

    phone = _norm_phone(request.form.get("phone", ""))
    if not phone:
        flash("Enter a valid Kenyan number (07… or 2547…).", "danger")
        return redirect(url_for("cashier.cashier_board"))

    already_paid = sum(float(p.amount or 0) for p in order.payments)
    due = round(float(order.total or 0) - already_paid, 2)
    if due <= 0:
        flash("Order already paid.", "warning")
        return redirect(url_for("cashier.cashier_board"))

    shortcode = os.environ.get("MPESA_SHORTCODE", "")
    passkey = os.environ.get("MPESA_PASSKEY", "")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
    amount = int(round(due))
    if amount < 1:
        amount = 1

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": os.environ.get("MPESA_CALLBACK_URL"),
        "AccountReference": (order.order_number or "PLATTER")[:12],
        "TransactionDesc": (order.order_number or "Platter")[:13],
    }

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
        flash("Could not reach M-Pesa. Use till + code instead.", "danger")
        return redirect(url_for("cashier.cashier_board"))

    if str(data.get("ResponseCode")) != "0":
        flash(
            data.get("errorMessage")
            or data.get("ResponseDescription")
            or "STK failed",
            "danger",
        )
        return redirect(url_for("cashier.cashier_board"))

    pending = Payment(
        meal_order_id=order.id,
        amount=Decimal(str(due)),
        method="mpesa_stk",
        phone=phone,
        checkout_request_id=data.get("CheckoutRequestID"),
        result_desc="pending",
        received_by_id=session.get("user_id"),
    )
    db.session.add(pending)
    db.session.commit()
    flash("Prompt sent. Ask the guest to enter PIN.", "success")
    return redirect(url_for("cashier.cashier_board"))

@cashier_bp.route("/mpesa/stk-callback", methods=["POST"])
def stk_callback():
    body = request.get_json(silent=True) or {}
    stk = (body.get("Body") or {}).get("stkCallback") or {}
    checkout_id = stk.get("CheckoutRequestID")
    result_code = stk.get("ResultCode")
    result_desc = stk.get("ResultDesc")

    payment = Payment.query.filter_by(checkout_request_id=checkout_id).first()
    if not payment:
        return {"ok": True}

    payment.result_desc = (result_desc or "")[:255]
    if result_code == 0:
        items = ((stk.get("CallbackMetadata") or {}).get("Item") or [])
        meta = {i.get("Name"): i.get("Value") for i in items}
        payment.mpesa_receipt = str(meta.get("MpesaReceiptNumber") or "")[:40]
        payment.reference = payment.mpesa_receipt
        order = payment.meal_order
        order.status = "closed"
        order.closed_at = datetime.utcnow()
        if order.table:
            order.table.status = "free"
    else:
        db.session.delete(payment)

    db.session.commit()
    return {"ok": True}
