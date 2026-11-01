"""SmartFlow Main and Dashboard Routes."""

from __future__ import annotations

from decimal import Decimal
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import desc

from generator.constants import COUNTRY_CURRENCIES
from models import (
    ApprovalStep,
    CostCenter,
    Employee,
    ExpenseClaim,
    LeaveRequest,
    Reimbursement,
    WorkflowEvent,
    db,
)
from services.expense_service import get_cost_center_budget_utilization

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@main_bp.route("/dashboard")
def dashboard():
    """Enterprise executive and workflow dashboard."""
    # Summary Metrics
    leave_total = LeaveRequest.query.count()
    leave_approved = LeaveRequest.query.filter_by(status="approved").count()
    leave_rejected = LeaveRequest.query.filter_by(status="rejected").count()
    leave_pending = LeaveRequest.query.filter_by(status="submitted").count()

    expense_total = ExpenseClaim.query.count()
    expense_approved = ExpenseClaim.query.filter_by(status="approved").count()
    expense_rejected = ExpenseClaim.query.filter_by(status="rejected").count()
    expense_pending = ExpenseClaim.query.filter_by(status="submitted").count()

    total_claims_amount = db.session.query(db.func.sum(ExpenseClaim.total_amount)).scalar() or Decimal("0.00")

    # Reimbursements
    reimb_total = Reimbursement.query.count()
    reimb_paid_count = Reimbursement.query.filter_by(status="paid").count()
    reimb_pending_count = Reimbursement.query.filter_by(status="pending").count()
    reimb_paid_amount = (
        db.session.query(db.func.sum(Reimbursement.amount)).filter_by(status="paid").scalar() or Decimal("0.00")
    )
    reimb_pending_amount = (
        db.session.query(db.func.sum(Reimbursement.amount)).filter_by(status="pending").scalar() or Decimal("0.00")
    )

    # Approvals overview
    step_total = ApprovalStep.query.count()
    step_approved = ApprovalStep.query.filter_by(decision="approved").count()
    step_rejected = ApprovalStep.query.filter_by(decision="rejected").count()
    step_pending = ApprovalStep.query.filter_by(decision="pending").count()

    # Cost Center Budget Utilization (Top 10 active)
    cost_centers = CostCenter.query.filter_by(is_active=True).order_by(CostCenter.code.asc()).all()
    budget_data = []
    for cc in cost_centers:
        util = get_cost_center_budget_utilization(cc.cost_center_id, year=2026)
        budget_data.append(
            {
                "cost_center_id": cc.cost_center_id,
                "code": cc.code,
                "name": cc.name,
                "country": cc.country,
                "currency": COUNTRY_CURRENCIES.get(cc.country, "USD"),
                "annual_budget": util["annual_budget"],
                "budget_used": util["budget_used"],
                "budget_remaining": util["budget_remaining"],
                "utilization_percentage": util["utilization_percentage"],
                "approved_claims_count": util["approved_claims_count"],
            }
        )

    # Sort budget data by utilization percentage descending
    budget_data.sort(key=lambda x: x["utilization_percentage"], reverse=True)

    # Recent Pending Approvals (top 6)
    recent_pending = (
        ApprovalStep.query.filter_by(decision="pending")
        .order_by(ApprovalStep.approval_step_id.desc())
        .limit(6)
        .all()
    )

    # Recent Audit Events (top 8)
    recent_events = (
        WorkflowEvent.query.order_by(WorkflowEvent.event_id.desc())
        .limit(8)
        .all()
    )

    return render_template(
        "dashboard.html",
        stats={
            "leave": {
                "total": leave_total,
                "approved": leave_approved,
                "rejected": leave_rejected,
                "pending": leave_pending,
            },
            "expense": {
                "total": expense_total,
                "approved": expense_approved,
                "rejected": expense_rejected,
                "pending": expense_pending,
                "total_amount": total_claims_amount,
            },
            "reimbursements": {
                "total": reimb_total,
                "paid_count": reimb_paid_count,
                "pending_count": reimb_pending_count,
                "paid_amount": reimb_paid_amount,
                "pending_amount": reimb_pending_amount,
            },
            "steps": {
                "total": step_total,
                "approved": step_approved,
                "rejected": step_rejected,
                "pending": step_pending,
            },
        },
        budget_data=budget_data,
        recent_pending=recent_pending,
        recent_events=recent_events,
    )


@main_bp.route("/switch-user", methods=["GET", "POST"])
def switch_user():
    """Switch active profile/role for interactive workflow simulation (Demo feature only, not production SSO)."""
    if request.method == "POST":
        user_id = request.form.get("employee_id")
    else:
        user_id = request.args.get("employee_id")

    if user_id:
        emp = Employee.query.filter_by(employee_id=user_id, is_active=True).first()
        if emp:
            session["user_id"] = emp.employee_id
            flash(f"[Demo Simulation] Switched active user to {emp.first_name} {emp.last_name} ({emp.job_title}, {emp.country}).", "info")
        else:
            flash(f"Employee '{user_id}' not found or inactive.", "danger")

    next_url = request.referrer or url_for("main.dashboard")
    return redirect(next_url)
