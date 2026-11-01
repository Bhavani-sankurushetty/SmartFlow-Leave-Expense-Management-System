"""SmartFlow Admin and Configuration Portal Routes."""

from __future__ import annotations

import os
from flask import Blueprint, flash, redirect, render_template, request, url_for

from config import Config
from models import (
    ApprovalRule,
    ApprovalStep,
    CostCenter,
    Employee,
    ExpenseClaim,
    ExpenseLine,
    ExpensePolicy,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    Reimbursement,
    WorkflowEvent,
    db,
)
from routes.employee import get_current_user

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def require_admin_or_executive():
    """Enforce Admin/Executive role access control on all administrative operations."""
    user = get_current_user()
    is_admin = user and (user.role == "Executive" or user.department in {"Operations", "Executive"})
    if not is_admin:
        flash(
            "Unauthorized: Administrative directory access is restricted to Executive and Operations personnel.",
            "danger",
        )
        return redirect(url_for("main.dashboard"))


@admin_bp.route("/")
def portal():
    """System administrative dashboard and database health."""
    db_uri = Config.SQLALCHEMY_DATABASE_URI
    db_file = db_uri.replace("sqlite:///", "")
    file_size = os.path.getsize(db_file) if os.path.exists(db_file) else 0

    entity_counts = {
        "employees": Employee.query.count(),
        "cost_centers": CostCenter.query.count(),
        "leave_policies": LeavePolicy.query.count(),
        "leave_balances": LeaveBalance.query.count(),
        "expense_policies": ExpensePolicy.query.count(),
        "approval_rules": ApprovalRule.query.count(),
        "leave_requests": LeaveRequest.query.count(),
        "expense_claims": ExpenseClaim.query.count(),
        "expense_lines": ExpenseLine.query.count(),
        "approval_steps": ApprovalStep.query.count(),
        "reimbursements": Reimbursement.query.count(),
        "workflow_events": WorkflowEvent.query.count(),
    }

    recent_events = WorkflowEvent.query.order_by(WorkflowEvent.event_id.desc()).limit(15).all()

    return render_template(
        "admin/portal.html",
        db_file=db_file,
        file_size=file_size,
        entity_counts=entity_counts,
        recent_events=recent_events,
        seed=Config.RANDOM_SEED,
    )


@admin_bp.route("/employees")
def employees():
    """Searchable list of organization employees."""
    search = request.args.get("q", "").strip()
    country_filter = request.args.get("country", "")

    query = Employee.query
    if search:
        query = query.filter(
            (Employee.first_name.ilike(f"%{search}%"))
            | (Employee.last_name.ilike(f"%{search}%"))
            | (Employee.email.ilike(f"%{search}%"))
            | (Employee.employee_id.ilike(f"%{search}%"))
            | (Employee.job_title.ilike(f"%{search}%"))
        )
    if country_filter:
        query = query.filter_by(country=country_filter)

    employees_list = query.order_by(Employee.employee_id.asc()).limit(100).all()
    total_count = query.count()

    return render_template(
        "admin/employees.html",
        employees=employees_list,
        total_count=total_count,
        search=search,
        country_filter=country_filter,
    )


@admin_bp.route("/policies")
def policies():
    """Master statutory and categorical policies."""
    leave_policies = LeavePolicy.query.order_by(LeavePolicy.country.asc(), LeavePolicy.name.asc()).all()
    expense_policies = ExpensePolicy.query.order_by(ExpensePolicy.country.asc(), ExpensePolicy.category.asc()).all()

    return render_template(
        "admin/policies.html",
        leave_policies=leave_policies,
        expense_policies=expense_policies,
    )


@admin_bp.route("/rules")
def rules():
    """Approval matrix rules."""
    req_type = request.args.get("request_type", "all")
    country = request.args.get("country", "all")

    query = ApprovalRule.query
    if req_type != "all":
        query = query.filter_by(request_type=req_type)
    if country != "all":
        query = query.filter_by(country=country)

    rules_list = query.order_by(
        ApprovalRule.country.asc(),
        ApprovalRule.request_type.asc(),
        ApprovalRule.authority_level.asc(),
    ).limit(155).all()

    return render_template(
        "admin/rules.html",
        rules=rules_list,
        req_type=req_type,
        country=country,
    )


@admin_bp.route("/cost-centers")
def cost_centers():
    """Cost center masters and budget allocations."""
    centers = CostCenter.query.order_by(CostCenter.code.asc()).all()
    return render_template("admin/cost_centers.html", cost_centers=centers)


@admin_bp.route("/audit-log")
def audit_log():
    """Audit log stream of workflow lifecycle events."""
    event_type = request.args.get("event_type", "all")
    query = WorkflowEvent.query
    if event_type != "all":
        query = query.filter_by(event_type=event_type)

    events = query.order_by(WorkflowEvent.event_id.desc()).limit(100).all()
    total_events = query.count()

    return render_template(
        "admin/audit_log.html",
        events=events,
        total_events=total_events,
        event_type=event_type,
    )
