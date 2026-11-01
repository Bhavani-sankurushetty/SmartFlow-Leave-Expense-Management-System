"""SmartFlow Employee Portal Routes."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from generator.constants import COUNTRY_CURRENCIES
from generator.id_generator import format_expense_claim_id, format_expense_line_id, format_leave_request_id
from models import (
    ApprovalStep,
    CostCenter,
    Employee,
    ExpenseClaim,
    ExpenseLine,
    ExpensePolicy,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    WorkflowEvent,
    db,
)
from services.leave_service import calculate_business_days, get_available_leave_balance
from services.workflow_service import get_workflow_state, submit_and_route_request

employee_bp = Blueprint("employee", __name__, url_prefix="/employee")


def get_current_user() -> Employee:
    """Helper to get current active employee from session or default."""
    user_id = session.get("user_id")
    emp = None
    if user_id:
        emp = Employee.query.filter_by(employee_id=user_id).first()
    if not emp:
        emp = Employee.query.filter_by(is_active=True).first()
        if emp:
            session["user_id"] = emp.employee_id
    return emp


@employee_bp.route("/", strict_slashes=False)
def hub():
    """Employee self-service dashboard."""
    user = get_current_user()
    if not user:
        flash("No active employee found in system.", "danger")
        return redirect(url_for("main.dashboard"))

    # My Leave Requests
    leaves = (
        LeaveRequest.query.filter_by(employee_id=user.employee_id)
        .order_by(LeaveRequest.start_date.desc())
        .all()
    )

    # My Expense Claims
    claims = (
        ExpenseClaim.query.filter_by(employee_id=user.employee_id)
        .order_by(ExpenseClaim.claim_date.desc())
        .all()
    )

    # My Leave Balances (Country matched)
    balances = (
        LeaveBalance.query.join(LeavePolicy, LeaveBalance.leave_policy_id == LeavePolicy.leave_policy_id)
        .filter(
            LeaveBalance.employee_id == user.employee_id,
            LeaveBalance.year == 2026,
            LeavePolicy.country == user.country,
        )
        .all()
    )

    # Calculate real-time available balances accounting for approved requests
    balance_cards = []
    for bal in balances:
        avail, _ = get_available_leave_balance(user.employee_id, bal.leave_policy_id, year=2026)
        balance_cards.append(
            {
                "policy_name": bal.leave_policy.name,
                "leave_type": bal.leave_policy.leave_type,
                "allowance": bal.leave_policy.annual_allowance_days,
                "accrued": bal.accrued_days,
                "used": bal.used_days,
                "remaining": bal.remaining_days,
                "available": avail,
            }
        )

    return render_template(
        "employee/hub.html",
        user=user,
        leaves=leaves,
        claims=claims,
        balances=balance_cards,
    )


@employee_bp.route("/leave/new", methods=["GET", "POST"])
def leave_new():
    """Submit a new leave request."""
    user = get_current_user()
    if not user:
        flash("No active employee found.", "danger")
        return redirect(url_for("main.dashboard"))

    # Available policies for user's country
    policies = LeavePolicy.query.filter_by(country=user.country, is_active=True).all()

    if request.method == "POST":
        policy_id = request.form.get("leave_policy_id")
        start_str = request.form.get("start_date")
        end_str = request.form.get("end_date")

        if not policy_id or not start_str or not end_str:
            flash("Please fill in all required leave request fields.", "warning")
            return redirect(url_for("employee.leave_new"))

        try:
            start_date = date.fromisoformat(start_str)
            end_date = date.fromisoformat(end_str)
        except ValueError:
            flash("Invalid date format provided.", "danger")
            return redirect(url_for("employee.leave_new"))

        if end_date < start_date:
            flash("Leave end date cannot be before start date.", "danger")
            return redirect(url_for("employee.leave_new"))

        # Compute business days
        business_days = calculate_business_days(start_date, end_date, user.country)
        if business_days <= Decimal("0.00"):
            flash("Selected date range contains 0 business days (spans only weekends or public holidays).", "danger")
            return redirect(url_for("employee.leave_new"))

        # Generate next ID
        count = LeaveRequest.query.count() + 1
        new_id = format_leave_request_id(2026, count)
        while LeaveRequest.query.filter_by(leave_request_id=new_id).first():
            count += 1
            new_id = format_leave_request_id(2026, count)

        req = LeaveRequest(
            leave_request_id=new_id,
            employee_id=user.employee_id,
            leave_policy_id=policy_id,
            start_date=start_date,
            end_date=end_date,
            business_days=business_days,
            status="submitted",
            submitted_at=datetime.now(timezone.utc),
        )
        db.session.add(req)
        db.session.commit()

        # Orchestrate end-to-end routing
        result = submit_and_route_request("leave", new_id)
        if result.get("can_route") and result.get("status") == "ROUTED":
            steps_cnt = result.get("steps_count", 0)
            flash(f"Leave request '{new_id}' successfully submitted and routed for approval ({steps_cnt} approval step(s) assigned).", "success")
        elif result.get("status") == "INELIGIBLE":
            flash(f"Leave request '{new_id}' was submitted but rejected: {result.get('reason')}", "danger")
        else:
            flash(f"Leave request '{new_id}' recorded with status: {result.get('status')}", "info")

        return redirect(url_for("employee.leave_detail", leave_id=new_id))

    return render_template("employee/leave_new.html", user=user, policies=policies)


@employee_bp.route("/expense/new", methods=["GET", "POST"])
def expense_new():
    """Submit a new multi-line expense claim."""
    user = get_current_user()
    if not user:
        flash("No active employee found.", "danger")
        return redirect(url_for("main.dashboard"))

    # Cost centers & Policies
    cost_centers = CostCenter.query.filter_by(is_active=True).all()
    policies = ExpensePolicy.query.filter_by(country=user.country, is_active=True).all()
    currency = COUNTRY_CURRENCIES.get(user.country, "USD")

    if request.method == "POST":
        cost_center_id = request.form.get("cost_center_id") or user.cost_center_id
        policy_id = request.form.get("expense_policy_id")
        claim_date_str = request.form.get("claim_date") or date.today().isoformat()

        # Line items
        descriptions = request.form.getlist("line_description[]")
        categories = request.form.getlist("line_category[]")
        dates = request.form.getlist("line_date[]")
        amounts = request.form.getlist("line_amount[]")

        if not policy_id or not descriptions or len(descriptions) == 0:
            flash("Please provide policy and at least one expense line.", "warning")
            return redirect(url_for("employee.expense_new"))

        try:
            claim_date = date.fromisoformat(claim_date_str)
        except ValueError:
            claim_date = date.today()

        # Generate claim ID
        claim_count = ExpenseClaim.query.count() + 1
        claim_id = format_expense_claim_id(2026, claim_count)
        while ExpenseClaim.query.filter_by(expense_claim_id=claim_id).first():
            claim_count += 1
            claim_id = format_expense_claim_id(2026, claim_count)

        total_amount = Decimal("0.00")
        lines_to_add = []

        for idx, (desc_text, cat, dt_str, amt_str) in enumerate(zip(descriptions, categories, dates, amounts), start=1):
            line_amt = Decimal(str(amt_str or "0.00")).quantize(Decimal("0.01"))
            total_amount += line_amt
            line_dt = date.fromisoformat(dt_str) if dt_str else claim_date

            # Receipt flag
            has_receipt = request.form.get(f"line_receipt_{idx}") == "1"
            if has_receipt:
                dummy_hash = hashlib.sha256(f"{claim_id}-{idx}-{line_amt}".encode()).hexdigest()
                desc_text = f"{desc_text} [Receipt: receipt_{idx}.pdf | SHA256: {dummy_hash}]"

            line_id = format_expense_line_id(claim_id, idx)
            line = ExpenseLine(
                expense_line_id=line_id,
                expense_claim_id=claim_id,
                line_number=idx,
                category=cat,
                description=desc_text,
                expense_date=line_dt,
                amount=line_amt,
                country=user.country,
            )
            lines_to_add.append(line)

        claim = ExpenseClaim(
            expense_claim_id=claim_id,
            employee_id=user.employee_id,
            cost_center_id=cost_center_id,
            expense_policy_id=policy_id,
            claim_date=claim_date,
            country=user.country,
            currency=currency,
            total_amount=total_amount,
            status="submitted",
            submitted_at=datetime.now(timezone.utc),
        )
        db.session.add(claim)
        for line in lines_to_add:
            db.session.add(line)
        db.session.commit()

        # Route claim
        result = submit_and_route_request("expense", claim_id)
        if result.get("can_route") and result.get("status") == "ROUTED":
            steps_cnt = result.get("steps_count", 0)
            flash(f"Expense claim '{claim_id}' ({currency} {total_amount}) submitted and routed for approval ({steps_cnt} step(s)).", "success")
        elif result.get("status") == "INELIGIBLE":
            flash(f"Expense claim '{claim_id}' was submitted but rejected: {result.get('reason')}", "danger")
        else:
            flash(f"Expense claim '{claim_id}' created with status: {result.get('status')}", "info")

        return redirect(url_for("employee.expense_detail", claim_id=claim_id))

    return render_template(
        "employee/expense_new.html",
        user=user,
        cost_centers=cost_centers,
        policies=policies,
        currency=currency,
    )


@employee_bp.route("/leave/<leave_id>")
def leave_detail(leave_id: str):
    """View leave request details, workflow state, and history."""
    req = LeaveRequest.query.filter_by(leave_request_id=leave_id).first_or_404()
    steps = (
        ApprovalStep.query.filter_by(leave_request_id=leave_id)
        .order_by(ApprovalStep.step_order.asc())
        .all()
    )
    events = (
        WorkflowEvent.query.filter_by(request_type="leave", entity_id=leave_id)
        .order_by(WorkflowEvent.event_id.asc())
        .all()
    )
    workflow_state = get_workflow_state("leave", leave_id)

    return render_template(
        "employee/leave_detail.html",
        req=req,
        steps=steps,
        events=events,
        workflow_state=workflow_state,
    )


@employee_bp.route("/expense/<claim_id>")
def expense_detail(claim_id: str):
    """View expense claim details, lines, reimbursement, and workflow."""
    claim = ExpenseClaim.query.filter_by(expense_claim_id=claim_id).first_or_404()
    lines = ExpenseLine.query.filter_by(expense_claim_id=claim_id).order_by(ExpenseLine.line_number.asc()).all()
    steps = (
        ApprovalStep.query.filter_by(expense_claim_id=claim_id)
        .order_by(ApprovalStep.step_order.asc())
        .all()
    )
    reimbursements = claim.reimbursements
    events = (
        WorkflowEvent.query.filter_by(request_type="expense", entity_id=claim_id)
        .order_by(WorkflowEvent.event_id.asc())
        .all()
    )
    workflow_state = get_workflow_state("expense", claim_id)

    return render_template(
        "employee/expense_detail.html",
        claim=claim,
        lines=lines,
        steps=steps,
        reimbursements=reimbursements,
        events=events,
        workflow_state=workflow_state,
    )
