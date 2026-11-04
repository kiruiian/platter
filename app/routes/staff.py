from flask import Blueprint, flash, redirect, render_template, request, url_for
from app.extensions import db
from app.models import HospitalStaff
from app.routes.auth import login_required

staff_bp = Blueprint("staff", __name__)

@staff_bp.route("/staff")
@login_required(roles={"admin"})
def staff_list():
    staff = HospitalStaff.query.filter_by(active=True).order_by(HospitalStaff.full_name).all()
    return render_template("staff.html", staff=staff)

@staff_bp.route("/staff", methods=["POST"])
@login_required(roles={"admin"})
def staff_create():
    full_name = request.form.get("full_name", "").strip()
    department = request.form.get("department", "").strip()
    employee_no = request.form.get("employee_no", "").strip()

    if not full_name:
        flash("Staff name is required.", "danger")
        return redirect(url_for("staff.staff_list"))

    db.session.add(HospitalStaff(
        full_name=full_name,
        department=department or None,
        employee_no=employee_no or None,
        active=True,
    ))
    db.session.commit()
    flash(f"{full_name} added.", "success")
    return redirect(url_for("staff.staff_list"))