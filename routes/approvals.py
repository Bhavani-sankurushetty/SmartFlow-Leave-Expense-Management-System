"""SmartFlow Approvals and Manager Portal Routes."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import ApprovalStep, Employee, ExpenseClaim, LeaveRequest, db
from routes.employee import get_current_user
from services.approval_service import ApprovalWorkflowError
from services.workflow_service import get_pending_tasks_for_approver, process_step_decision

approvals_bp = Blueprint("approvals", __name__, url_prefix="/approvals")


@approvals_bp.route("/")
def inbox():
    """Approver actionable inbox."""
    user = get_current_user()
    if not user:
        flash("No active user session found.", "danger")
        return redirect(url_for("main.dashboard"))

    # Active pending approval steps assigned to this approver
    pending_steps = (
        ApprovalStep.query.filter_by(approver_employee_id=user.employee_id, decision="pending")
        .order_by(ApprovalStep.step_order.asc(), ApprovalStep.approval_step_id.asc())
        .all()
    )

    # History of decided steps by this approver
    decided_history = (
        ApprovalStep.query.filter(
            ApprovalStep.approver_employee_id == user.employee_id,
            ApprovalStep.decision.in_(["approved", "rejected"]),
        )
        .order_by(ApprovalStep.decided_at.desc())
        .limit(25)
        .all()
    )

    # Format pending tasks list with submitter and amount info
    tasks = []
    for s in pending_steps:
        req_type = "leave" if s.leave_request_id else "expense"
        parent_id = s.leave_request_id or s.expense_claim_id
        submitter = None
        details = ""
        amount_display = ""

        if req_type == "leave" and s.leave_request:
            submitter = s.leave_request.employee
            details = f"{s.leave_request.leave_policy.name} ({s.leave_request.business_days} days: {s.leave_request.start_date} to {s.leave_request.end_date})"
            amount_display = f"{s.leave_request.business_days} Days"
        elif req_type == "expense" and s.expense_claim:
            submitter = s.expense_claim.employee
            details = f"{s.expense_claim.expense_policy.category} Claim on {s.expense_claim.claim_date} ({len(s.expense_claim.expense_lines)} line items)"
            amount_display = f"{s.expense_claim.currency} {s.expense_claim.total_amount}"

        tasks.append(
            {
                "step": s,
                "request_type": req_type,
                "parent_id": parent_id,
                "submitter": submitter,
                "details": details,
                "amount_display": amount_display,
                "authority_level": s.approval_rule.authority_level if s.approval_rule else f"Level {s.step_order}",
                "step_order": s.step_order,
            }
        )

    return render_template(
        "approvals/inbox.html",
        user=user,
        tasks=tasks,
        history=decided_history,
    )


@approvals_bp.route("/review/<step_id>")
def review(step_id: str):
    """Review details for a specific pending approval step."""
    user = get_current_user()
    step = ApprovalStep.query.filter_by(approval_step_id=step_id).first_or_404()

    req_type = "leave" if step.leave_request_id else "expense"
    parent = step.leave_request if req_type == "leave" else step.expense_claim

    # Get sibling steps
    if req_type == "leave":
        all_steps = ApprovalStep.query.filter_by(leave_request_id=step.leave_request_id).order_by(ApprovalStep.step_order.asc()).all()
    else:
        all_steps = ApprovalStep.query.filter_by(expense_claim_id=step.expense_claim_id).order_by(ApprovalStep.step_order.asc()).all()

    return render_template(
        "approvals/review.html",
        user=user,
        step=step,
        req_type=req_type,
        parent=parent,
        all_steps=all_steps,
    )


@approvals_bp.route("/decide", methods=["POST"])
def decide():
    """Process an approval or rejection decision on an approval step."""
    user = get_current_user()
    step_id = request.form.get("approval_step_id")
    decision = request.form.get("decision")  # 'approved' or 'rejected'
    comments = request.form.get("comments") or ""

    if not step_id or decision not in {"approved", "rejected"}:
        flash("Invalid decision parameters submitted.", "danger")
        return redirect(url_for("approvals.inbox"))

    try:
        res = process_step_decision(step_id, user.employee_id, decision, comments)
        decision_label = "Approved" if decision == "approved" else "Rejected"
        if res.get("request_completed"):
            flash(
                f"Step {step_id} {decision_label}. Workflow completed: parent request status is now '{res.get('parent_request_status')}'.",
                "success" if decision == "approved" else "warning",
            )
        else:
            flash(
                f"Step {step_id} {decision_label}. Advanced to next approval level ({res.get('next_step_order')}).",
                "success",
            )
    except ApprovalWorkflowError as exc:
        flash(f"Approval action failed: {exc.message} (Code: {exc.code})", "danger")
    except Exception as exc:
        flash(f"Unexpected error processing decision: {exc}", "danger")

    return redirect(url_for("approvals.inbox"))
