"""Reimbursement lifecycle service for SmartFlow.

Phase 9: Budget-Aware Approval Workflow Integration and Reimbursement Lifecycle.
Enforces business rules for expense claim reimbursement generation, payment execution,
auditable event history, state transitions, duplicate prevention, and transaction safety.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from generator.id_generator import format_reimbursement_id
from models import ApprovalStep, ExpenseClaim, Reimbursement, WorkflowEvent, db


REIMBURSEMENT_ID_REGEX = re.compile(r"^RMB-\d{4}-\d{6}$")


def utc_now() -> datetime:
    """Return current UTC timestamp without timezone offset for database compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """Convert datetime to naive UTC for consistent comparison across database engines."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class ReimbursementError(Exception):
    """Base exception for reimbursement lifecycle violations."""

    def __init__(self, message: str, code: str = "REIMBURSEMENT_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


def derive_reimbursement_id(claim: ExpenseClaim) -> str:
    """Derive deterministic reimbursement ID following 'RMB-YYYY-NNNNNN' convention."""
    year = claim.claim_date.year if claim.claim_date else 2026
    match = re.search(r"EXP-(\d{4})-(\d+)", claim.expense_claim_id)
    if match:
        seq = int(match.group(2))
        candidate = format_reimbursement_id(year, seq)
        if not Reimbursement.query.filter_by(reimbursement_id=candidate).first():
            return candidate

    # Fallback to next sequence strictly greater than any existing in database
    existing_reimbs = Reimbursement.query.filter(
        Reimbursement.reimbursement_id.like(f"RMB-{year}-%")
    ).all()
    max_seq = 0
    for r in existing_reimbs:
        parts = r.reimbursement_id.split("-")
        if len(parts) == 3 and parts[2].isdigit():
            max_seq = max(max_seq, int(parts[2]))
    return format_reimbursement_id(year, max_seq + 1)


def create_reimbursement_for_approved_claim(
    expense_claim_id: str,
    reimbursement_id: str | None = None,
    created_at: datetime | None = None,
) -> Reimbursement:
    """Create and persist a reimbursement for a finalized, approved expense claim.

    Business Rules:
    1. A reimbursement may be created only when the expense claim is finally approved.
    2. All required approval steps must be approved.
    3. Reimbursement amount must equal the approved claim amount.
    4. Uses deterministic reimbursement ID convention: 'RMB-2026-000001'.
    5. Prevents duplicate reimbursements for the same claim.
    6. Never reimburses pending or rejected claims.
    7. Records creation timestamp.
    8. Maintains auditable workflow event trail.
    9. Executes within a database transaction with safe rollback on failure.
    """
    claim = ExpenseClaim.query.filter_by(expense_claim_id=expense_claim_id).first()
    if not claim:
        raise ReimbursementError(
            f"Expense claim '{expense_claim_id}' not found.",
            "CLAIM_NOT_FOUND",
        )

    # Rule 1 & 6: Claim must be approved
    if claim.status != "approved":
        raise ReimbursementError(
            f"Cannot reimburse expense claim '{expense_claim_id}' with status '{claim.status}'. Must be 'approved'.",
            "CLAIM_NOT_APPROVED",
        )

    # Rule 2: All required approval steps must be approved
    steps = (
        ApprovalStep.query.filter_by(expense_claim_id=expense_claim_id)
        .order_by(ApprovalStep.step_order.asc())
        .all()
    )
    if not steps:
        raise ReimbursementError(
            f"Cannot reimburse expense claim '{expense_claim_id}': no approval steps found.",
            "MISSING_APPROVAL_STEPS",
        )

    unapproved_steps = [s for s in steps if s.decision != "approved"]
    if unapproved_steps:
        unapproved_orders = [s.step_order for s in unapproved_steps]
        raise ReimbursementError(
            f"Cannot reimburse expense claim '{expense_claim_id}': required step(s) {unapproved_orders} are not approved.",
            "UNAPPROVED_STEPS_REMAIN",
        )

    # Rule 5: Prevent duplicate reimbursements
    existing_reimb = Reimbursement.query.filter_by(expense_claim_id=expense_claim_id).first()
    if existing_reimb:
        raise ReimbursementError(
            f"Reimbursement already exists for expense claim '{expense_claim_id}': '{existing_reimb.reimbursement_id}'.",
            "DUPLICATE_REIMBURSEMENT",
        )

    # Rule 4: Deterministic reimbursement ID validation
    target_id = reimbursement_id or derive_reimbursement_id(claim)
    if not REIMBURSEMENT_ID_REGEX.match(target_id):
        raise ReimbursementError(
            f"Invalid reimbursement ID '{target_id}'. Must follow format 'RMB-YYYY-NNNNNN'.",
            "INVALID_REIMBURSEMENT_ID",
        )

    id_collision = Reimbursement.query.filter_by(reimbursement_id=target_id).first()
    if id_collision:
        raise ReimbursementError(
            f"Reimbursement ID '{target_id}' already exists in database.",
            "DUPLICATE_REIMBURSEMENT_ID",
        )

    now = to_naive_utc(created_at) or utc_now()
    # Rule 3: Reimbursement amount must equal claim total
    amount = Decimal(str(claim.total_amount))

    try:
        reimbursement = Reimbursement(
            reimbursement_id=target_id,
            expense_claim_id=expense_claim_id,
            amount=amount,
            status="pending",
            paid_at=None,
            created_at=now,
            updated_at=now,
        )
        db.session.add(reimbursement)

        # Rule 8 & 9: Auditable workflow event
        event = WorkflowEvent(
            event_id=f"EVT-{target_id}-CREATE",
            request_type="reimbursement",
            entity_id=target_id,
            event_type="REIMBURSEMENT_CREATED",
            actor_employee_id=claim.employee_id,
            from_status=None,
            to_status="pending",
            payload_json=json.dumps(
                {
                    "expense_claim_id": expense_claim_id,
                    "amount": str(amount),
                    "currency": claim.currency,
                    "created_at": now.isoformat(),
                }
            ),
            created_at=now,
        )
        db.session.add(event)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ReimbursementError(
            f"Failed to create reimbursement for claim '{expense_claim_id}': {exc}",
            "DATABASE_ERROR",
        ) from exc

    return reimbursement


def get_reimbursement(reimbursement_id: str) -> Reimbursement | None:
    """Retrieve reimbursement by ID."""
    return Reimbursement.query.filter_by(reimbursement_id=reimbursement_id).first()


def get_reimbursement_history(expense_claim_id: str) -> list[dict[str, Any]]:
    """Retrieve reimbursement records and audit history for an expense claim."""
    reimbursements = (
        Reimbursement.query.filter_by(expense_claim_id=expense_claim_id)
        .order_by(Reimbursement.created_at.asc())
        .all()
    )

    history = []
    for r in reimbursements:
        history.append(
            {
                "reimbursement_id": r.reimbursement_id,
                "expense_claim_id": r.expense_claim_id,
                "amount": Decimal(str(r.amount)),
                "currency": r.expense_claim.currency if r.expense_claim else "USD",
                "status": r.status,
                "created_at": r.created_at,
                "paid_at": r.paid_at,
                "updated_at": r.updated_at,
            }
        )
    return history


def mark_reimbursement_paid(
    reimbursement_id: str,
    paid_at: datetime | None = None,
) -> Reimbursement:
    """Mark a pending reimbursement as paid with timestamp validation and audit logging.

    Business Rules:
    1. Reimbursement must exist and be in 'pending' status.
    2. Do not mark a reimbursement paid more than once.
    3. Payment timestamp must be on or after creation timestamp.
    4. Auditable state transition logged.
    5. Database transaction with safe rollback.
    """
    reimb = Reimbursement.query.filter_by(reimbursement_id=reimbursement_id).first()
    if not reimb:
        raise ReimbursementError(
            f"Reimbursement '{reimbursement_id}' not found.",
            "REIMBURSEMENT_NOT_FOUND",
        )

    # Rule 7: Do not mark paid more than once
    if reimb.status == "paid":
        raise ReimbursementError(
            f"Reimbursement '{reimbursement_id}' is already paid at {reimb.paid_at}.",
            "ALREADY_PAID",
        )

    if reimb.status != "pending":
        raise ReimbursementError(
            f"Cannot pay reimbursement '{reimbursement_id}' with status '{reimb.status}'. Must be 'pending'.",
            "INVALID_STATUS",
        )

    payment_time = to_naive_utc(paid_at) or utc_now()
    reimb_created = to_naive_utc(reimb.created_at)
    if payment_time < reimb_created:
        raise ReimbursementError(
            f"Payment timestamp ({payment_time}) cannot be earlier than creation timestamp ({reimb.created_at}).",
            "INVALID_PAYMENT_DATE",
        )

    try:
        reimb.status = "paid"
        reimb.paid_at = payment_time
        reimb.updated_at = payment_time

        # Auditable event
        event = WorkflowEvent(
            event_id=f"EVT-{reimbursement_id}-PAID",
            request_type="reimbursement",
            entity_id=reimbursement_id,
            event_type="REIMBURSEMENT_PAID",
            actor_employee_id=None,
            from_status="pending",
            to_status="paid",
            payload_json=json.dumps(
                {
                    "expense_claim_id": reimb.expense_claim_id,
                    "amount": str(reimb.amount),
                    "paid_at": payment_time.isoformat(),
                }
            ),
            created_at=payment_time,
        )
        db.session.add(event)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ReimbursementError(
            f"Failed to mark reimbursement '{reimbursement_id}' paid: {exc}",
            "DATABASE_ERROR",
        ) from exc

    return reimb
