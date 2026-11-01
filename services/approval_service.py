"""Approval routing service for SmartFlow.

Phase 7: Approval Routing.
Determines required approval routes, creates approval steps, enforces multi-level
hierarchical approval workflows, records approval/rejection decisions, and safely
transitions request lifecycles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from generator.constants import AUTHORITY_LEVELS
from generator.id_generator import format_approval_step_id
from models import (
    ApprovalRule,
    ApprovalStep,
    CostCenter,
    Employee,
    ExpenseClaim,
    LeaveRequest,
    db,
)
from services.expense_service import evaluate_expense_claim
from services.leave_service import evaluate_leave_request


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


class ApprovalWorkflowError(Exception):
    """Base exception for approval workflow errors."""

    def __init__(self, message: str, code: str = "WORKFLOW_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


def resolve_approver(
    authority_level: int,
    employee: Employee,
    cost_center: CostCenter | None = None,
    previous_approver_ids: set[str] | None = None,
) -> tuple[Employee | None, str | None]:
    """Resolve an approver employee for a given authority level.

    Authority Levels:
    1: Manager (Direct manager)
    2: Department Head (2nd-level manager or department Director)
    3: Finance (Cost Center Owner or Finance Manager/Director)
    4: Senior Management (Executive / VP in chain or CEO)

    Returns (approver_employee, error_reason).
    """
    if previous_approver_ids is None:
        previous_approver_ids = set()

    submitter_id = employee.employee_id

    if authority_level == 1:
        # Level 1: Direct Manager
        if not employee.manager_id:
            return None, f"Employee '{employee.employee_id}' has no designated manager."
        mgr = Employee.query.filter_by(employee_id=employee.manager_id).first()
        if not mgr:
            return None, f"Manager ID '{employee.manager_id}' not found in database."
        if not mgr.is_active:
            return None, f"Direct manager '{mgr.employee_id}' ({mgr.first_name} {mgr.last_name}) is inactive."
        if mgr.employee_id == submitter_id:
            return None, f"Employee cannot be their own manager (self-approval prohibited)."
        return mgr, None

    elif authority_level == 2:
        # Level 2: Department Head
        # Try 1: Next manager up the reporting line (manager's manager)
        direct_mgr = employee.manager
        if direct_mgr and direct_mgr.manager_id:
            m2 = Employee.query.filter_by(employee_id=direct_mgr.manager_id).first()
            if m2 and m2.is_active and m2.employee_id != submitter_id and m2.employee_id not in previous_approver_ids:
                return m2, None

        # Try 2: Director of the employee's department
        director = Employee.query.filter(
            Employee.department == employee.department,
            Employee.job_title.like("Director%"),
            Employee.is_active.is_(True),
        ).first()
        if director and director.employee_id != submitter_id and director.employee_id not in previous_approver_ids:
            return director, None

        # Try 3: VP / Executive in the department
        vp = Employee.query.filter(
            Employee.department == employee.department,
            Employee.job_title.like("VP%"),
            Employee.is_active.is_(True),
        ).first()
        if vp and vp.employee_id != submitter_id and vp.employee_id not in previous_approver_ids:
            return vp, None

        # Fallback: Escalate up direct manager chain until active non-self approver found
        curr = direct_mgr
        while curr and curr.manager_id:
            curr = Employee.query.filter_by(employee_id=curr.manager_id).first()
            if curr and curr.is_active and curr.employee_id != submitter_id and curr.employee_id not in previous_approver_ids:
                return curr, None

        return None, f"Could not find an active Department Head for department '{employee.department}'."

    elif authority_level == 3:
        # Level 3: Finance
        # Try 1: Cost Center Owner
        if cost_center and cost_center.owner_employee_id:
            cc_owner = Employee.query.filter_by(employee_id=cost_center.owner_employee_id).first()
            if (
                cc_owner
                and cc_owner.is_active
                and cc_owner.employee_id != submitter_id
                and cc_owner.employee_id not in previous_approver_ids
            ):
                return cc_owner, None

        # Try 2: Finance Department Manager / Director
        finance_approver = Employee.query.filter(
            Employee.department == "Finance",
            (Employee.job_title.like("%Manager%") | Employee.job_title.like("Director%") | Employee.job_title.like("VP%")),
            Employee.is_active.is_(True),
        ).first()
        if finance_approver and finance_approver.employee_id != submitter_id and finance_approver.employee_id not in previous_approver_ids:
            return finance_approver, None

        return None, "Could not resolve an active Finance approver or Cost Center owner."

    elif authority_level == 4:
        # Level 4: Senior Management
        # Try 1: CEO
        ceo = Employee.query.filter_by(job_title="Chief Executive Officer", is_active=True).first()
        if ceo and ceo.employee_id != submitter_id and ceo.employee_id not in previous_approver_ids:
            return ceo, None

        # Try 2: Any active Executive / VP not already assigned
        exec_approver = Employee.query.filter(
            Employee.job_title.like("VP%"),
            Employee.is_active.is_(True),
            Employee.employee_id != submitter_id,
        ).first()
        if exec_approver and exec_approver.employee_id not in previous_approver_ids:
            return exec_approver, None

        return None, "Could not resolve an active Senior Management approver."

    return None, f"Unsupported authority level: {authority_level}"


def resolve_approval_route(
    request_type: str,
    request_id: str,
    skip_eligibility_check: bool = False,
) -> dict[str, Any]:
    """Resolve the required approval route for a leave request or expense claim.

    Returns a structured dictionary:
    - status: 'VALID', 'INELIGIBLE', 'NO_MATCHING_RULE', 'INACTIVE_OR_MISSING_APPROVER', 'CONFLICTING_RULES', or 'NOT_FOUND'.
    - can_route: Boolean indicating if approval steps can be safely generated.
    - request_type: 'leave' or 'expense'.
    - request_id: Target request ID.
    - employee_id: Submitting employee ID.
    - amount: Numeric amount or business days.
    - explanation: Narrative explaining the routing determination.
    - steps: List of required approval step dictionaries.
    """
    request_type = request_type.lower().strip()
    if request_type not in {"leave", "expense"}:
        return {
            "status": "INVALID_REQUEST_TYPE",
            "can_route": False,
            "request_type": request_type,
            "request_id": request_id,
            "employee_id": None,
            "amount": None,
            "explanation": f"Unknown request type '{request_type}'. Expected 'leave' or 'expense'.",
            "steps": [],
        }

    # 1. Fetch request and validate eligibility
    if request_type == "leave":
        req = LeaveRequest.query.filter_by(leave_request_id=request_id).first()
        if not req:
            return {
                "status": "NOT_FOUND",
                "can_route": False,
                "request_type": "leave",
                "request_id": request_id,
                "employee_id": None,
                "amount": None,
                "explanation": f"Leave request '{request_id}' not found.",
                "steps": [],
            }
        employee = req.employee
        amount = Decimal(str(req.business_days))
        country = employee.country if employee else None
        category = None
        cost_center = employee.cost_center if employee else None

        if not skip_eligibility_check:
            eval_res = evaluate_leave_request(req)
            if eval_res.get("decision") == "REJECT":
                return {
                    "status": "INELIGIBLE",
                    "can_route": False,
                    "request_type": "leave",
                    "request_id": request_id,
                    "employee_id": req.employee_id,
                    "amount": amount,
                    "explanation": f"Leave request failed eligibility: {eval_res.get('reason')} ({eval_res.get('reason_code')})",
                    "steps": [],
                }
    else:
        claim = ExpenseClaim.query.filter_by(expense_claim_id=request_id).first()
        if not claim:
            return {
                "status": "NOT_FOUND",
                "can_route": False,
                "request_type": "expense",
                "request_id": request_id,
                "employee_id": None,
                "amount": None,
                "explanation": f"Expense claim '{request_id}' not found.",
                "steps": [],
            }
        employee = claim.employee
        amount = Decimal(str(claim.total_amount))
        country = claim.country or (employee.country if employee else None)
        category = claim.expense_policy.category if claim.expense_policy else None
        cost_center = claim.cost_center

        if not skip_eligibility_check:
            eval_res = evaluate_expense_claim(claim)
            if eval_res.get("decision") == "REJECT":
                return {
                    "status": "INELIGIBLE",
                    "can_route": False,
                    "request_type": "expense",
                    "request_id": request_id,
                    "employee_id": claim.employee_id,
                    "amount": amount,
                    "explanation": f"Expense claim failed eligibility: {eval_res.get('reason')} ({eval_res.get('reason_code')})",
                    "steps": [],
                }

    if not employee:
        return {
            "status": "INACTIVE_OR_MISSING_APPROVER",
            "can_route": False,
            "request_type": request_type,
            "request_id": request_id,
            "employee_id": None,
            "amount": amount,
            "explanation": "Submitting employee record not found.",
            "steps": [],
        }

    # 2. Query matching approval rules
    rule_query = ApprovalRule.query.filter(
        ApprovalRule.request_type == request_type,
        ApprovalRule.country == country,
        ApprovalRule.is_active.is_(True),
    )
    if request_type == "expense":
        rule_query = rule_query.filter(ApprovalRule.category == category)
    else:
        rule_query = rule_query.filter(ApprovalRule.category.is_(None))

    candidate_rules = rule_query.all()
    if not candidate_rules:
        return {
            "status": "NO_MATCHING_RULE",
            "can_route": False,
            "request_type": request_type,
            "request_id": request_id,
            "employee_id": employee.employee_id,
            "amount": amount,
            "explanation": f"No active approval rules found for {request_type} in country '{country}' (category: {category}).",
            "steps": [],
        }

    # Find the specific rule threshold that covers this amount
    matching_threshold_rules = [
        r
        for r in candidate_rules
        if Decimal(str(r.min_amount)) <= amount and (r.max_amount is None or amount <= Decimal(str(r.max_amount)))
    ]

    if not matching_threshold_rules:
        return {
            "status": "NO_MATCHING_RULE",
            "can_route": False,
            "request_type": request_type,
            "request_id": request_id,
            "employee_id": employee.employee_id,
            "amount": amount,
            "explanation": f"Amount {amount} does not match any approval band for country '{country}'.",
            "steps": [],
        }

    if len(matching_threshold_rules) > 1:
        # Detect conflicting / ambiguous routing rules
        return {
            "status": "CONFLICTING_RULES",
            "can_route": False,
            "request_type": request_type,
            "request_id": request_id,
            "employee_id": employee.employee_id,
            "amount": amount,
            "explanation": f"Ambiguous approval rules: multiple active rules ({[r.approval_rule_id for r in matching_threshold_rules]}) cover amount {amount}.",
            "steps": [],
        }

    target_rule = matching_threshold_rules[0]
    required_max_step_order = target_rule.step_order

    # Retrieve all sequential approval steps up to required_max_step_order
    route_rules = [r for r in candidate_rules if r.step_order <= required_max_step_order]
    route_rules.sort(key=lambda r: r.step_order)

    # 3. Resolve approvers for each step
    planned_steps = []
    assigned_approver_ids = set()

    for rule in route_rules:
        approver, err = resolve_approver(
            rule.authority_level,
            employee,
            cost_center=cost_center,
            previous_approver_ids=assigned_approver_ids,
        )
        if not approver or err:
            return {
                "status": "INACTIVE_OR_MISSING_APPROVER",
                "can_route": False,
                "request_type": request_type,
                "request_id": request_id,
                "employee_id": employee.employee_id,
                "amount": amount,
                "explanation": f"Routing failed at step {rule.step_order} (Authority Level {rule.authority_level}): {err}",
                "steps": [],
            }

        assigned_approver_ids.add(approver.employee_id)
        role_name = AUTHORITY_LEVELS.get(rule.authority_level, "Approver")
        planned_steps.append(
            {
                "step_order": rule.step_order,
                "approval_rule_id": rule.approval_rule_id,
                "authority_level": rule.authority_level,
                "authority_role": role_name,
                "approver_employee_id": approver.employee_id,
                "approver_name": f"{approver.first_name} {approver.last_name}",
                "approver_title": approver.job_title,
                "approver_department": approver.department,
            }
        )

    explanation = (
        f"Request of {amount} in {country} matched rule {target_rule.approval_rule_id} "
        f"(Level {target_rule.authority_level} - {AUTHORITY_LEVELS.get(target_rule.authority_level, 'Approver')}), "
        f"requiring {len(planned_steps)} sequential approval step(s)."
    )

    return {
        "status": "VALID",
        "can_route": True,
        "request_type": request_type,
        "request_id": request_id,
        "employee_id": employee.employee_id,
        "amount": amount,
        "explanation": explanation,
        "steps": planned_steps,
    }


def create_approval_steps(request_type: str, request_id: str) -> list[ApprovalStep]:
    """Generate and persist approval steps for a submitted request within a database transaction.

    Safeguards:
    - Request must exist and be in 'submitted' status.
    - Cannot create duplicate approval steps if steps already exist.
    - Validates routing; if routing fails, rolls back and raises ApprovalWorkflowError.
    """
    request_type = request_type.lower().strip()
    if request_type not in {"leave", "expense"}:
        raise ApprovalWorkflowError(f"Invalid request type '{request_type}'.", "INVALID_REQUEST_TYPE")

    if request_type == "leave":
        req = LeaveRequest.query.filter_by(leave_request_id=request_id).first()
        if not req:
            raise ApprovalWorkflowError(f"Leave request '{request_id}' not found.", "REQUEST_NOT_FOUND")
        if req.status in {"approved", "rejected"}:
            raise ApprovalWorkflowError(
                f"Cannot create approval steps for leave request '{request_id}' with status '{req.status}'.",
                "INVALID_STATE_TRANSITION",
            )
        existing_steps = ApprovalStep.query.filter_by(leave_request_id=request_id).all()
    else:
        claim = ExpenseClaim.query.filter_by(expense_claim_id=request_id).first()
        if not claim:
            raise ApprovalWorkflowError(f"Expense claim '{request_id}' not found.", "REQUEST_NOT_FOUND")
        if claim.status in {"approved", "rejected"}:
            raise ApprovalWorkflowError(
                f"Cannot create approval steps for expense claim '{request_id}' with status '{claim.status}'.",
                "INVALID_STATE_TRANSITION",
            )
        existing_steps = ApprovalStep.query.filter_by(expense_claim_id=request_id).all()

    if existing_steps:
        raise ApprovalWorkflowError(
            f"Approval steps already exist for {request_type} request '{request_id}' ({len(existing_steps)} steps found).",
            "DUPLICATE_STEPS",
        )

    route = resolve_approval_route(request_type, request_id)
    if not route.get("can_route"):
        raise ApprovalWorkflowError(
            f"Cannot create approval steps: {route.get('explanation')}",
            route.get("status", "ROUTING_FAILED"),
        )

    created_steps = []
    try:
        for s in route["steps"]:
            step_id = format_approval_step_id(request_id, s["step_order"])
            new_step = ApprovalStep(
                approval_step_id=step_id,
                leave_request_id=request_id if request_type == "leave" else None,
                expense_claim_id=request_id if request_type == "expense" else None,
                approval_rule_id=s["approval_rule_id"],
                approver_employee_id=s["approver_employee_id"],
                step_order=s["step_order"],
                decision="pending",
                comments=None,
                decided_at=None,
                created_at=utc_now(),
            )
            db.session.add(new_step)
            created_steps.append(new_step)

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ApprovalWorkflowError(f"Failed to persist approval steps: {exc}", "DATABASE_ERROR") from exc

    return created_steps


def get_pending_approvals(
    approver_employee_id: str | None = None,
    request_type: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve actionable pending approval steps.

    Only steps whose prior steps are all 'approved' are considered actionable.
    """
    query = ApprovalStep.query.filter_by(decision="pending")
    if approver_employee_id:
        query = query.filter_by(approver_employee_id=approver_employee_id)

    pending_steps = query.order_by(ApprovalStep.created_at.asc()).all()
    actionable = []

    for step in pending_steps:
        # Check prior steps
        if step.leave_request_id:
            curr_type = "leave"
            req_id = step.leave_request_id
            prior_steps = ApprovalStep.query.filter(
                ApprovalStep.leave_request_id == req_id,
                ApprovalStep.step_order < step.step_order,
            ).all()
        else:
            curr_type = "expense"
            req_id = step.expense_claim_id
            prior_steps = ApprovalStep.query.filter(
                ApprovalStep.expense_claim_id == req_id,
                ApprovalStep.step_order < step.step_order,
            ).all()

        if request_type and curr_type != request_type.lower().strip():
            continue

        # If any prior step is not approved, this step is not yet actionable
        if not all(p.decision == "approved" for p in prior_steps):
            continue

        actionable.append(
            {
                "approval_step_id": step.approval_step_id,
                "request_type": curr_type,
                "request_id": req_id,
                "step_order": step.step_order,
                "approver_employee_id": step.approver_employee_id,
                "approval_rule_id": step.approval_rule_id,
                "created_at": step.created_at,
            }
        )

    return actionable


def record_decision(
    approval_step_id: str,
    approver_employee_id: str,
    decision: str,
    comments: str | None = None,
) -> dict[str, Any]:
    """Record an approval or rejection decision on an assigned approval step within a transaction.

    Safeguards:
    - Step must exist.
    - Decision must be 'approved' or 'rejected'.
    - Step must currently be 'pending' (no duplicate decisions).
    - Approver must match assigned approver_employee_id (unauthorized approver rejected).
    - All prior steps must be 'approved' (cannot decide out of sequence).
    - Parent request must not already be 'approved' or 'rejected'.
    - If rejected: step and parent request marked 'rejected'.
    - If approved: step marked 'approved'; if final step, parent request marked 'approved'.
    """
    decision = decision.lower().strip()
    if decision not in {"approved", "rejected"}:
        raise ApprovalWorkflowError(
            f"Invalid decision '{decision}'. Expected 'approved' or 'rejected'.",
            "INVALID_DECISION",
        )

    step = ApprovalStep.query.filter_by(approval_step_id=approval_step_id).first()
    if not step:
        raise ApprovalWorkflowError(f"Approval step '{approval_step_id}' not found.", "STEP_NOT_FOUND")

    # Safeguard 1: Duplicate decision check
    if step.decision != "pending":
        raise ApprovalWorkflowError(
            f"Approval step '{approval_step_id}' has already been decided with '{step.decision}' at {step.decided_at}.",
            "STEP_ALREADY_DECIDED",
        )

    # Safeguard 2: Authorization check
    if step.approver_employee_id != approver_employee_id:
        raise ApprovalWorkflowError(
            f"Employee '{approver_employee_id}' is not authorized to decide step '{approval_step_id}' "
            f"(assigned to '{step.approver_employee_id}').",
            "UNAUTHORIZED_APPROVER",
        )

    # Safeguard 3: Sequential step order check
    if step.leave_request_id:
        req_type = "leave"
        req_id = step.leave_request_id
        parent_req = LeaveRequest.query.filter_by(leave_request_id=req_id).first()
        all_steps = ApprovalStep.query.filter_by(leave_request_id=req_id).order_by(ApprovalStep.step_order.asc()).all()
    else:
        req_type = "expense"
        req_id = step.expense_claim_id
        parent_req = ExpenseClaim.query.filter_by(expense_claim_id=req_id).first()
        all_steps = ApprovalStep.query.filter_by(expense_claim_id=req_id).order_by(ApprovalStep.step_order.asc()).all()

    if not parent_req:
        raise ApprovalWorkflowError(f"Parent {req_type} request '{req_id}' not found.", "REQUEST_NOT_FOUND")

    # Safeguard 4: Parent request status check
    if parent_req.status in {"approved", "rejected"}:
        raise ApprovalWorkflowError(
            f"Cannot decide step on {req_type} request '{req_id}' because request is already '{parent_req.status}'.",
            "INVALID_STATE_TRANSITION",
        )

    # Check that all prior steps are already approved
    prior_unapproved = [s for s in all_steps if s.step_order < step.step_order and s.decision != "approved"]
    if prior_unapproved:
        raise ApprovalWorkflowError(
            f"Cannot decide step {step.step_order} because prior step(s) {[s.step_order for s in prior_unapproved]} are not yet approved.",
            "PENDING_PRIOR_STEPS",
        )

    now = utc_now()
    try:
        step.decision = decision
        step.decided_at = now
        step.comments = comments

        if decision == "rejected":
            # Immediate rejection of parent request; subsequent steps will not proceed
            parent_req.status = "rejected"
            parent_req.decided_at = now
            request_completed = True
            final_status = "rejected"
        else:
            # Check if this was the last step
            remaining_pending = [s for s in all_steps if s.approval_step_id != step.approval_step_id and s.decision == "pending"]
            if not remaining_pending:
                parent_req.status = "approved"
                parent_req.decided_at = now
                request_completed = True
                final_status = "approved"
            else:
                request_completed = False
                final_status = parent_req.status

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ApprovalWorkflowError(f"Database error recording decision: {exc}", "DATABASE_ERROR") from exc

    return {
        "approval_step_id": step.approval_step_id,
        "request_type": req_type,
        "request_id": req_id,
        "step_order": step.step_order,
        "decision": decision,
        "decided_at": now,
        "request_completed": request_completed,
        "parent_request_status": final_status,
        "comments": comments,
    }


def record_approval(approval_step_id: str, approver_employee_id: str, comments: str | None = None) -> dict[str, Any]:
    """Record an approval decision for an approval step."""
    return record_decision(approval_step_id, approver_employee_id, "approved", comments)


def record_rejection(approval_step_id: str, approver_employee_id: str, comments: str | None = None) -> dict[str, Any]:
    """Record a rejection decision for an approval step."""
    return record_decision(approval_step_id, approver_employee_id, "rejected", comments)


def is_approval_complete(request_type: str, request_id: str) -> bool:
    """Determine whether all required approval levels for a request are complete and approved."""
    request_type = request_type.lower().strip()
    if request_type == "leave":
        steps = ApprovalStep.query.filter_by(leave_request_id=request_id).all()
        req = LeaveRequest.query.filter_by(leave_request_id=request_id).first()
    else:
        steps = ApprovalStep.query.filter_by(expense_claim_id=request_id).all()
        req = ExpenseClaim.query.filter_by(expense_claim_id=request_id).first()

    if not req or not steps:
        return False

    return req.status == "approved" and all(s.decision == "approved" for s in steps)


def get_request_approval_history(request_type: str, request_id: str) -> list[dict[str, Any]]:
    """Retrieve the full approval step history for a request."""
    request_type = request_type.lower().strip()
    if request_type == "leave":
        steps = ApprovalStep.query.filter_by(leave_request_id=request_id).order_by(ApprovalStep.step_order.asc()).all()
    else:
        steps = ApprovalStep.query.filter_by(expense_claim_id=request_id).order_by(ApprovalStep.step_order.asc()).all()

    history = []
    for s in steps:
        approver = s.approver
        history.append(
            {
                "approval_step_id": s.approval_step_id,
                "step_order": s.step_order,
                "authority_level": s.approval_rule.authority_level if s.approval_rule else None,
                "approver_employee_id": s.approver_employee_id,
                "approver_name": f"{approver.first_name} {approver.last_name}" if approver else None,
                "decision": s.decision,
                "comments": s.comments,
                "decided_at": s.decided_at,
                "created_at": s.created_at,
            }
        )
    return history
