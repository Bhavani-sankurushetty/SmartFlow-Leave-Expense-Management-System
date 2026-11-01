"""SmartFlow Finance and Reimbursement Portal Routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from flask import Blueprint, flash, redirect, render_template, request, url_for

from generator.constants import COUNTRY_CURRENCIES
from models import CostCenter, ExpenseClaim, Reimbursement, db
from routes.employee import get_current_user
from services.expense_service import get_cost_center_budget_utilization
from services.reimbursement_service import ReimbursementError, mark_reimbursement_paid

finance_bp = Blueprint("finance", __name__, url_prefix="/finance")


@finance_bp.route("/")
def portal():
    """Finance portal overview and reimbursement tracking."""
    status_filter = request.args.get("status", "all")

    # Metrics
    total_reimbursements = Reimbursement.query.count()
    paid_count = Reimbursement.query.filter_by(status="paid").count()
    pending_count = Reimbursement.query.filter_by(status="pending").count()

    paid_sum = (
        db.session.query(db.func.sum(Reimbursement.amount)).filter_by(status="paid").scalar() or Decimal("0.00")
    )
    pending_sum = (
        db.session.query(db.func.sum(Reimbursement.amount)).filter_by(status="pending").scalar() or Decimal("0.00")
    )
    total_sum = (
        db.session.query(db.func.sum(Reimbursement.amount)).scalar() or Decimal("0.00")
    )

    # Query reimbursements
    q = Reimbursement.query
    if status_filter in {"pending", "paid"}:
        q = q.filter_by(status=status_filter)

    reimbursements = q.order_by(Reimbursement.created_at.desc()).limit(100).all()

    # Budget summary across cost centers
    cost_centers = CostCenter.query.filter_by(is_active=True).all()
    budget_overview = []
    for cc in cost_centers:
        util = get_cost_center_budget_utilization(cc.cost_center_id, 2026)
        budget_overview.append(
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
            }
        )

    return render_template(
        "finance/portal.html",
        stats={
            "total_count": total_reimbursements,
            "paid_count": paid_count,
            "pending_count": pending_count,
            "total_amount": total_sum,
            "paid_amount": paid_sum,
            "pending_amount": pending_sum,
        },
        reimbursements=reimbursements,
        budget_overview=budget_overview,
        status_filter=status_filter,
    )


@finance_bp.route("/reimbursements/<reimbursement_id>/pay", methods=["POST"])
def pay(reimbursement_id: str):
    """Mark a pending reimbursement voucher as paid."""
    user = get_current_user()
    is_finance = user and (user.department == "Finance" or user.role == "Executive")
    if not is_finance:
        flash(
            "Unauthorized: Only authorized Finance personnel or Executives may process reimbursement disbursements.",
            "danger",
        )
        return redirect(url_for("finance.portal"))

    try:
        reimb = Reimbursement.query.filter_by(reimbursement_id=reimbursement_id).first()
        paid_time = None
        if reimb and reimb.created_at:
            now_utc = datetime.now(timezone.utc)
            created_utc = reimb.created_at.replace(tzinfo=timezone.utc) if reimb.created_at.tzinfo is None else reimb.created_at
            if now_utc < created_utc:
                paid_time = created_utc + timedelta(seconds=1)
        res = mark_reimbursement_paid(reimbursement_id, paid_at=paid_time)
        flash(
            f"Reimbursement voucher '{reimbursement_id}' ({res.amount}) successfully marked as PAID. Disbursement logged.",
            "success",
        )
    except ReimbursementError as exc:
        flash(f"Payment action rejected: {exc.message} (Code: {exc.code})", "danger")
    except Exception as exc:
        flash(f"Unexpected payment error: {exc}", "danger")

    return redirect(url_for("finance.portal"))


@finance_bp.route("/budgets")
def budgets():
    """Cost center budget management and overrun analysis."""
    cost_centers = CostCenter.query.filter_by(is_active=True).order_by(CostCenter.code.asc()).all()
    budgets_list = []
    for cc in cost_centers:
        util = get_cost_center_budget_utilization(cc.cost_center_id, 2026)
        budgets_list.append(
            {
                "cost_center_id": cc.cost_center_id,
                "code": cc.code,
                "name": cc.name,
                "country": cc.country,
                "owner": cc.owner,
                "currency": COUNTRY_CURRENCIES.get(cc.country, "USD"),
                "annual_budget": util["annual_budget"],
                "budget_used": util["budget_used"],
                "budget_remaining": util["budget_remaining"],
                "utilization_percentage": util["utilization_percentage"],
                "approved_claims_count": util["approved_claims_count"],
            }
        )

    return render_template("finance/budgets.html", budgets=budgets_list)
