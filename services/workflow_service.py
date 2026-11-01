"""End-to-end workflow orchestration service for SmartFlow.

Phase 9: Budget-Aware Approval Workflow Integration and Reimbursement Lifecycle.
Orchestrates eligibility checks, deterministic routing, sequential approval execution,
auditable lifecycle transitions, event trail recording, and reimbursement triggering.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from models import (
    ApprovalStep,
    ExpenseClaim,
    LeaveRequest,
    WorkflowEvent,
    db,
)
from services.approval_service import (
    ApprovalWorkflowError,
    create_approval_steps,
    get_pending_approvals,
    get_request_approval_history,
    is_approval_complete,
    record_approval,
    record_decision,
    record_rejection,
    resolve_approval_route,
)
from services.expense_service import evaluate_expense_claim
from services.leave_service import evaluate_leave_request
from services.reimbursement_service import (
    create_reimbursement_for_approved_claim,
    get_reimbursement_history,
)


def utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def record_workflow_event(
    request_type: str,
    entity_id: str,
    event_type: str,
    actor_employee_id: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict[str, Any] | None = None,
    event_time: datetime | None = None,
) -> WorkflowEvent:
    """Record an append-only audit event for workflow state transitions."""
    now = event_time or utc_now()
    event_id = f"EVT-{entity_id}-{now.strftime('%Y%m%d%H%M%S%f')}"
    event = WorkflowEvent(
        event_id=event_id,
        request_type=request_type,
        entity_id=entity_id,
        event_type=event_type,
        actor_employee_id=actor_employee_id,
        from_status=from_status,
        to_status=to_status,
        payload_json=json.dumps(payload, default=str) if payload else None,
        created_at=now,
    )
    db.session.add(event)
    return event


def submit_and_route_request(
    request_type: str,
    request_id: str,
    actor_employee_id: str | None = None,
) -> dict[str, Any]:
    """Validate eligibility, resolve route, and create sequential approval steps.

    Workflow Rules:
    1. Evaluates leave or expense eligibility before routing.
    2. Keeps ineligible requests out of the approval workflow (marks them rejected).
    3. Prevents duplicate approval steps (idempotent retries).
    4. Routes only eligible submitted requests.
    5. Records auditable workflow events for submission, eligibility, routing, and step creation.
    """
    request_type = request_type.lower().strip()
    if request_type not in {"leave", "expense"}:
        raise ApprovalWorkflowError(f"Invalid request type '{request_type}'.", "INVALID_REQUEST_TYPE")

    # 1. Fetch parent request
    if request_type == "leave":
        req = LeaveRequest.query.filter_by(leave_request_id=request_id).first()
        if not req:
            raise ApprovalWorkflowError(f"Leave request '{request_id}' not found.", "REQUEST_NOT_FOUND")
        existing_steps = (
            ApprovalStep.query.filter_by(leave_request_id=request_id)
            .order_by(ApprovalStep.step_order.asc())
            .all()
        )
        submitter_id = req.employee_id
        parent_status = req.status
    else:
        claim = ExpenseClaim.query.filter_by(expense_claim_id=request_id).first()
        if not claim:
            raise ApprovalWorkflowError(f"Expense claim '{request_id}' not found.", "REQUEST_NOT_FOUND")
        existing_steps = (
            ApprovalStep.query.filter_by(expense_claim_id=request_id)
            .order_by(ApprovalStep.step_order.asc())
            .all()
        )
        submitter_id = claim.employee_id
        parent_status = claim.status

    actor_id = actor_employee_id or submitter_id

    # 2. Idempotency safeguard: if steps already exist, return existing routing
    if existing_steps:
        return {
            "status": "ALREADY_ROUTED",
            "can_route": True,
            "request_type": request_type,
            "request_id": request_id,
            "is_eligible": True,
            "steps_count": len(existing_steps),
            "steps": [s.approval_step_id for s in existing_steps],
            "message": f"Request '{request_id}' has already been routed ({len(existing_steps)} steps exist).",
        }

    # 3. Request status check: cannot route finalized requests
    if parent_status in {"approved", "rejected"}:
        raise ApprovalWorkflowError(
            f"Cannot route {request_type} request '{request_id}' with terminal status '{parent_status}'.",
            "INVALID_STATE_TRANSITION",
        )

    # Record REQUEST_SUBMITTED event
    record_workflow_event(
        request_type=request_type,
        entity_id=request_id,
        event_type="REQUEST_SUBMITTED",
        actor_employee_id=actor_id,
        from_status=None,
        to_status="submitted",
        payload={"request_id": request_id, "submitter_id": submitter_id},
    )

    # 4. Evaluate eligibility before routing
    if request_type == "leave":
        eval_res = evaluate_leave_request(request_id)
    else:
        eval_res = evaluate_expense_claim(request_id)

    record_workflow_event(
        request_type=request_type,
        entity_id=request_id,
        event_type="ELIGIBILITY_EVALUATED",
        actor_employee_id=None,
        from_status="submitted",
        to_status="rejected" if eval_res.get("decision") == "REJECT" else "submitted",
        payload=eval_res,
    )

    # If ineligible: keep out of workflow, reject parent request, record decision
    if eval_res.get("decision") == "REJECT":
        now = utc_now()
        if request_type == "leave":
            req.status = "rejected"
            req.decided_at = now
        else:
            claim.status = "rejected"
            claim.decided_at = now

        record_workflow_event(
            request_type=request_type,
            entity_id=request_id,
            event_type="FINAL_DECISION",
            actor_employee_id=None,
            from_status="submitted",
            to_status="rejected",
            payload={
                "decision": "REJECT",
                "reason_code": eval_res.get("reason_code"),
                "reason": eval_res.get("reason"),
            },
            event_time=now,
        )
        db.session.commit()

        return {
            "status": "INELIGIBLE",
            "can_route": False,
            "request_type": request_type,
            "request_id": request_id,
            "is_eligible": False,
            "reason_code": eval_res.get("reason_code"),
            "reason": eval_res.get("reason"),
            "steps": [],
        }

    # 5. Resolve approval route
    route = resolve_approval_route(request_type, request_id)
    record_workflow_event(
        request_type=request_type,
        entity_id=request_id,
        event_type="ROUTE_RESOLVED",
        actor_employee_id=None,
        from_status="submitted",
        to_status="submitted",
        payload={
            "status": route.get("status"),
            "can_route": route.get("can_route"),
            "explanation": route.get("explanation"),
            "steps_count": len(route.get("steps", [])),
        },
    )

    if not route.get("can_route"):
        return {
            "status": route.get("status", "ROUTING_FAILED"),
            "can_route": False,
            "request_type": request_type,
            "request_id": request_id,
            "is_eligible": True,
            "explanation": route.get("explanation"),
            "steps": [],
        }

    # 6. Create and persist approval steps
    steps = create_approval_steps(request_type, request_id)

    for step in steps:
        record_workflow_event(
            request_type=request_type,
            entity_id=step.approval_step_id,
            event_type="STEP_CREATED",
            actor_employee_id=step.approver_employee_id,
            from_status=None,
            to_status="pending",
            payload={
                "step_order": step.step_order,
                "approval_rule_id": step.approval_rule_id,
                "approver_employee_id": step.approver_employee_id,
            },
        )
    db.session.commit()

    return {
        "status": "ROUTED",
        "can_route": True,
        "request_type": request_type,
        "request_id": request_id,
        "is_eligible": True,
        "steps_count": len(steps),
        "steps": [s.approval_step_id for s in steps],
        "explanation": route.get("explanation"),
    }


def process_step_decision(
    approval_step_id: str,
    approver_employee_id: str,
    decision: str,
    comments: str | None = None,
) -> dict[str, Any]:
    """Execute approval or rejection on an approval step with strict authorization safeguards.

    Safeguards:
    1. Step must exist and be in 'pending' status.
    2. Approver must match the assigned approver (prevent unauthorized approver).
    3. Self-approval is strictly prevented (submitter cannot approve own request).
    4. Prior approval steps must all be approved (prevents out-of-order decision).
    5. Idempotent: re-submitting identical decision on already-decided step returns existing state.
    6. Transition updates parent request status.
    7. Logs append-only workflow audit events for decisions and final completion.
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

    # Idempotent return if already decided identically
    if step.decision == decision:
        return {
            "approval_step_id": step.approval_step_id,
            "request_type": "leave" if step.leave_request_id else "expense",
            "request_id": step.leave_request_id or step.expense_claim_id,
            "step_order": step.step_order,
            "decision": step.decision,
            "decided_at": step.decided_at,
            "comments": step.comments,
            "is_idempotent_retry": True,
        }

    if step.decision != "pending":
        raise ApprovalWorkflowError(
            f"Approval step '{approval_step_id}' has already been decided with '{step.decision}'.",
            "STEP_ALREADY_DECIDED",
        )

    # Submitter identity check for self-approval prevention
    submitter_id = (
        step.leave_request.employee_id
        if step.leave_request_id
        else step.expense_claim.employee_id
    )
    if approver_employee_id == submitter_id:
        raise ApprovalWorkflowError(
            f"Employee '{approver_employee_id}' cannot approve their own request (self-approval prohibited).",
            "SELF_APPROVAL_PROHIBITED",
        )

    # Authorization check
    if approver_employee_id != step.approver_employee_id:
        raise ApprovalWorkflowError(
            f"Employee '{approver_employee_id}' is not authorized to decide step '{approval_step_id}' "
            f"(assigned to '{step.approver_employee_id}').",
            "UNAUTHORIZED_APPROVER",
        )

    # Execute decision via approval_service
    result = record_decision(
        approval_step_id=approval_step_id,
        approver_employee_id=approver_employee_id,
        decision=decision,
        comments=comments,
    )

    req_type = result["request_type"]
    req_id = result["request_id"]
    now = result["decided_at"]

    # Record STEP_DECIDED event
    record_workflow_event(
        request_type=req_type,
        entity_id=approval_step_id,
        event_type="STEP_DECIDED",
        actor_employee_id=approver_employee_id,
        from_status="pending",
        to_status=decision,
        payload={"comments": comments, "decided_at": now.isoformat()},
        event_time=now,
    )

    # If this step finalized the parent request, record FINAL_DECISION
    if result.get("request_completed"):
        final_status = result["parent_request_status"]
        record_workflow_event(
            request_type=req_type,
            entity_id=req_id,
            event_type="FINAL_DECISION",
            actor_employee_id=approver_employee_id,
            from_status="submitted",
            to_status=final_status,
            payload={
                "parent_request_status": final_status,
                "decided_at": now.isoformat(),
                "final_approver": approver_employee_id,
            },
            event_time=now,
        )

    db.session.commit()
    return result


def get_workflow_state(request_type: str, request_id: str) -> dict[str, Any]:
    """Retrieve full workflow state, approval steps, actionable items, and event history."""
    request_type = request_type.lower().strip()
    if request_type == "leave":
        req = LeaveRequest.query.filter_by(leave_request_id=request_id).first()
        if not req:
            return {"error": f"Leave request '{request_id}' not found."}
        submitter_id = req.employee_id
        parent_status = req.status
        amount = Decimal(str(req.business_days))
        unit = "days"
        reimb_history = []
    else:
        claim = ExpenseClaim.query.filter_by(expense_claim_id=request_id).first()
        if not claim:
            return {"error": f"Expense claim '{request_id}' not found."}
        submitter_id = claim.employee_id
        parent_status = claim.status
        amount = Decimal(str(claim.total_amount))
        unit = claim.currency
        reimb_history = get_reimbursement_history(request_id)

    approval_history = get_request_approval_history(request_type, request_id)
    is_complete = is_approval_complete(request_type, request_id)

    # Audit events
    events = (
        WorkflowEvent.query.filter(
            WorkflowEvent.request_type.in_([request_type, "reimbursement"]),
            WorkflowEvent.entity_id.like(f"%{request_id}%"),
        )
        .order_by(WorkflowEvent.created_at.asc())
        .all()
    )

    event_timeline = [
        {
            "event_id": e.event_id,
            "event_type": e.event_type,
            "actor_employee_id": e.actor_employee_id,
            "from_status": e.from_status,
            "to_status": e.to_status,
            "created_at": e.created_at,
            "payload": json.loads(e.payload_json) if e.payload_json else {},
        }
        for e in events
    ]

    return {
        "request_id": request_id,
        "request_type": request_type,
        "submitter_id": submitter_id,
        "status": parent_status,
        "amount": amount,
        "unit": unit,
        "is_approval_complete": is_complete,
        "approval_steps": approval_history,
        "reimbursements": reimb_history,
        "event_timeline": event_timeline,
    }


def get_pending_tasks_for_approver(
    approver_employee_id: str,
    request_type: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve actionable pending approval tasks for an approver employee."""
    pending_steps = get_pending_approvals(
        approver_employee_id=approver_employee_id,
        request_type=request_type,
    )
    enriched = []
    for s in pending_steps:
        req_type = s["request_type"]
        req_id = s["request_id"]
        if req_type == "leave":
            req = LeaveRequest.query.filter_by(leave_request_id=req_id).first()
            submitter = req.employee if req else None
            summary = f"{req.business_days} days leave ({req.leave_policy.leave_type})" if req else ""
        else:
            claim = ExpenseClaim.query.filter_by(expense_claim_id=req_id).first()
            submitter = claim.employee if claim else None
            summary = f"{claim.total_amount} {claim.currency} expense ({claim.expense_policy.category})" if claim else ""

        enriched.append(
            {
                "approval_step_id": s["approval_step_id"],
                "request_type": req_type,
                "request_id": req_id,
                "step_order": s["step_order"],
                "submitter_employee_id": submitter.employee_id if submitter else None,
                "submitter_name": f"{submitter.first_name} {submitter.last_name}" if submitter else None,
                "submitter_department": submitter.department if submitter else None,
                "request_summary": summary,
                "created_at": s["created_at"],
            }
        )
    return enriched
