"""Leave request routing, balance calculations, and evaluation service.

Phase 5: Leave Business Logic.
Evaluates leave requests against employee tenure, leave policy constraints,
statutory country holiday calendars, available leave balances, and approved leave overlaps.
Does NOT modify database records or create approval steps automatically.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from generator.constants import COUNTRY_HOLIDAYS_2026
from models import Employee, LeaveBalance, LeavePolicy, LeaveRequest, db

TWO_PLACES = Decimal("0.01")


def calculate_business_days(start_date: date, end_date: date, country: str) -> Decimal:
    """Calculate working days between start_date and end_date (inclusive).

    Excludes weekends (Saturday, Sunday) and documented statutory public holidays for the country.
    Returns 0.00 if start_date > end_date.
    """
    if start_date > end_date:
        return Decimal("0.00")

    holidays = COUNTRY_HOLIDAYS_2026.get(country, set())
    current = start_date
    working_days = 0

    while current <= end_date:
        if current.weekday() < 5 and current not in holidays:
            working_days += 1
        current += timedelta(days=1)

    return Decimal(working_days).quantize(TWO_PLACES)


def get_available_leave_balance(
    employee_id: str,
    leave_policy_id: str,
    year: int = 2026,
    exclude_request_id: str | None = None,
) -> tuple[Decimal, LeaveBalance | None]:
    """Calculate the available leave balance for an employee, policy, and year.

    Data maintenance logic:
    1. Read the employee's LeaveBalance snapshot for (employee_id, leave_policy_id, year).
       In the master data model, remaining_days = opening_balance + accrued_days - used_days.
    2. To avoid double-counting, used_days is NOT deducted a second time because it is
       already factored into remaining_days.
    3. Query all approved leave requests in leave_requests for this employee and policy
       in the specified year (excluding exclude_request_id if re-evaluating an existing request).
    4. Available days = max(0.00, remaining_days - sum(other_approved_request_business_days)).
    """
    balance_record = LeaveBalance.query.filter_by(
        employee_id=employee_id,
        leave_policy_id=leave_policy_id,
        year=year,
    ).first()

    if balance_record is None:
        return Decimal("0.00"), None

    base_remaining = Decimal(str(balance_record.remaining_days))

    # Account for approved requests in the database
    query = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.leave_policy_id == leave_policy_id,
        LeaveRequest.status == "approved",
        db.extract("year", LeaveRequest.start_date) == year,
    )
    if exclude_request_id:
        query = query.filter(LeaveRequest.leave_request_id != exclude_request_id)

    approved_requests = query.all()
    approved_days = sum(Decimal(str(r.business_days)) for r in approved_requests)

    available_days = max(Decimal("0.00"), base_remaining - approved_days)
    return available_days.quantize(TWO_PLACES), balance_record


def check_leave_overlap(
    employee_id: str,
    start_date: date,
    end_date: date,
    exclude_request_id: str | None = None,
) -> LeaveRequest | None:
    """Check if the requested date range overlaps with any already approved leave request.

    Returns the conflicting LeaveRequest record, or None if no overlap exists.
    """
    query = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == "approved",
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    )
    if exclude_request_id:
        query = query.filter(LeaveRequest.leave_request_id != exclude_request_id)

    return query.first()


def evaluate_leave_request(
    request: LeaveRequest | dict | str,
    enforce_country_match: bool = True,
    auto_approve: bool = False,
) -> dict:
    """Evaluate a leave request against business policy, tenure, overlap, and balance criteria.

    Accepts:
    - A LeaveRequest model instance.
    - A dictionary containing request attributes.
    - A leave_request_id string to load from the database.

    Returns a structured decision dictionary:
    - request_id: Identifier of the request (or None).
    - employee_id: Identifier of the employee.
    - decision: 'APPROVE', 'REJECT', or 'NEEDS_REVIEW'.
    - reason_code: Programmatic code explaining the decision.
    - reason: Human-readable explanation.
    - requested_days: Business days requested (Decimal).
    - available_days: Available balance days (Decimal).
    - policy_limit: Applicable policy consecutive days limit (Decimal or None).
    - evaluated_at: UTC timestamp of evaluation.

    Behavior:
    - Distinguishes eligibility from final approval. Valid unapproved requests return
      'NEEDS_REVIEW' with code 'ELIGIBLE_FOR_APPROVAL' rather than auto-approving.
    - Safe to run repeatedly; performs zero database updates.
    """
    # 1. Normalize input
    if isinstance(request, str):
        req_obj = LeaveRequest.query.filter_by(leave_request_id=request).first()
        if not req_obj:
            return {
                "request_id": request,
                "employee_id": None,
                "decision": "REJECT",
                "reason_code": "REQUEST_NOT_FOUND",
                "reason": f"Leave request '{request}' was not found in the database.",
                "requested_days": Decimal("0.00"),
                "available_days": Decimal("0.00"),
                "policy_limit": None,
                "evaluated_at": datetime.now(timezone.utc),
            }
        req_id = req_obj.leave_request_id
        emp_id = req_obj.employee_id
        pol_id = req_obj.leave_policy_id
        start_dt = req_obj.start_date
        end_dt = req_obj.end_date
        status = req_obj.status
    elif isinstance(request, dict):
        req_id = request.get("request_id") or request.get("leave_request_id")
        emp_id = request.get("employee_id")
        pol_id = request.get("leave_policy_id")
        start_dt = request.get("start_date")
        end_dt = request.get("end_date")
        status = request.get("status", "submitted")
    else:
        req_id = getattr(request, "leave_request_id", None) or getattr(request, "request_id", None)
        emp_id = getattr(request, "employee_id", None)
        pol_id = getattr(request, "leave_policy_id", None)
        start_dt = getattr(request, "start_date", None)
        end_dt = getattr(request, "end_date", None)
        status = getattr(request, "status", "submitted")

    evaluated_at = datetime.now(timezone.utc)

    # 2. Employee existence check
    employee = Employee.query.filter_by(employee_id=emp_id).first() if emp_id else None
    if not employee:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "EMPLOYEE_NOT_FOUND",
            "reason": f"Employee '{emp_id}' does not exist in the database.",
            "requested_days": Decimal("0.00"),
            "available_days": Decimal("0.00"),
            "policy_limit": None,
            "evaluated_at": evaluated_at,
        }

    # 3. Leave policy existence and status check
    policy = LeavePolicy.query.filter_by(leave_policy_id=pol_id).first() if pol_id else None
    if not policy or not policy.is_active:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "POLICY_NOT_FOUND",
            "reason": f"Leave policy '{pol_id}' does not exist or is inactive.",
            "requested_days": Decimal("0.00"),
            "available_days": Decimal("0.00"),
            "policy_limit": None,
            "evaluated_at": evaluated_at,
        }

    policy_limit = Decimal(str(policy.max_consecutive_days)) if policy.max_consecutive_days is not None else None

    # 4. Country and policy matching
    if enforce_country_match and policy.country != employee.country:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "COUNTRY_POLICY_MISMATCH",
            "reason": f"Leave policy country ({policy.country}) does not match employee country ({employee.country}).",
            "requested_days": Decimal("0.00"),
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    # 5. Date range ordering check
    if not start_dt or not end_dt or start_dt > end_dt:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "INVALID_DATE_RANGE",
            "reason": f"Leave start date ({start_dt}) cannot be after end date ({end_dt}).",
            "requested_days": Decimal("0.00"),
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    # 6. Employment tenure boundaries
    if start_dt < employee.hire_date:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "OUTSIDE_EMPLOYMENT_PERIOD",
            "reason": f"Leave start date ({start_dt}) is before employee hire date ({employee.hire_date}).",
            "requested_days": Decimal("0.00"),
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    if employee.termination_date and end_dt > employee.termination_date:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "OUTSIDE_EMPLOYMENT_PERIOD",
            "reason": f"Leave end date ({end_dt}) is after employee termination date ({employee.termination_date}).",
            "requested_days": Decimal("0.00"),
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    # 7. Business days calculation (excluding weekends and statutory holidays)
    requested_days = calculate_business_days(start_dt, end_dt, employee.country)
    if requested_days <= Decimal("0.00"):
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "INVALID_DATE_RANGE",
            "reason": "Requested leave period contains 0 business days (spans only weekends or statutory holidays).",
            "requested_days": requested_days,
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    # 8. Policy maximum consecutive days limit
    if policy_limit is not None and requested_days > policy_limit:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "POLICY_LIMIT_EXCEEDED",
            "reason": f"Requested days ({requested_days}) exceeds policy maximum consecutive days limit ({policy_limit}).",
            "requested_days": requested_days,
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    # 9. Overlapping approved leave check
    conflict = check_leave_overlap(emp_id, start_dt, end_dt, exclude_request_id=req_id)
    if conflict:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "OVERLAPPING_APPROVED_LEAVE",
            "reason": (
                f"Requested leave dates ({start_dt} to {end_dt}) overlap with an already approved "
                f"leave request '{conflict.leave_request_id}' ({conflict.start_date} to {conflict.end_date})."
            ),
            "requested_days": requested_days,
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    # 10. Available balance check
    available_days, balance_record = get_available_leave_balance(
        emp_id, pol_id, year=start_dt.year, exclude_request_id=req_id
    )

    if not balance_record or available_days <= Decimal("0.00"):
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "NO_AVAILABLE_BALANCE",
            "reason": f"Employee has zero remaining leave balance for policy '{policy.name}'.",
            "requested_days": requested_days,
            "available_days": Decimal("0.00"),
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    if requested_days > available_days:
        return {
            "request_id": req_id,
            "employee_id": emp_id,
            "decision": "REJECT",
            "reason_code": "BALANCE_EXCEEDED",
            "reason": f"Requested days ({requested_days}) exceeds available leave balance ({available_days}).",
            "requested_days": requested_days,
            "available_days": available_days,
            "policy_limit": policy_limit,
            "evaluated_at": evaluated_at,
        }

    # 11. Eligibility passed
    if status == "approved" or auto_approve:
        decision = "APPROVE"
        reason = f"Leave request is valid, approved, and fully covered by available balance ({available_days} days available)."
    else:
        decision = "NEEDS_REVIEW"
        reason = (
            f"Leave request meets all policy, date, and balance criteria ({available_days} days available) "
            f"and is eligible for approval, pending manager review."
        )

    return {
        "request_id": req_id,
        "employee_id": emp_id,
        "decision": decision,
        "reason_code": "ELIGIBLE_FOR_APPROVAL",
        "reason": reason,
        "requested_days": requested_days,
        "available_days": available_days,
        "policy_limit": policy_limit,
        "evaluated_at": evaluated_at,
    }
