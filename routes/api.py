"""SmartFlow Client JSON API Routes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from flask import Blueprint, jsonify, request

from generator.constants import COUNTRY_CURRENCIES, COUNTRY_HOLIDAYS_2026
from models import CostCenter, Employee, ExpensePolicy, LeavePolicy
from routes.employee import get_current_user
from services.expense_service import get_cost_center_budget_utilization
from services.leave_service import calculate_business_days, evaluate_leave_request, get_available_leave_balance

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/calculate-leave", methods=["GET", "POST"])
def calculate_leave():
    """Dynamically calculate business days and available balance for a proposed leave span."""
    user = get_current_user()

    if request.is_json:
        payload = request.get_json() or {}
        emp_id = payload.get("employee_id") or (user.employee_id if user else None)
        policy_id = payload.get("policy_id") or payload.get("leave_policy_id")
        start_str = payload.get("start_date")
        end_str = payload.get("end_date")
    else:
        emp_id = request.args.get("employee_id") or (user.employee_id if user else None)
        policy_id = request.args.get("policy_id") or request.args.get("leave_policy_id")
        start_str = request.args.get("start_date")
        end_str = request.args.get("end_date")

    if not emp_id or not start_str or not end_str:
        return jsonify({"status": "error", "success": False, "error": "Missing required query parameters."}), 400

    employee = Employee.query.filter_by(employee_id=emp_id).first()
    if not employee:
        return jsonify({"status": "error", "success": False, "error": "Employee not found."}), 404

    # Default policy to first active policy for employee's country if not specified
    if not policy_id:
        policy = LeavePolicy.query.filter_by(country=employee.country, is_active=True).first()
    else:
        policy = LeavePolicy.query.filter_by(leave_policy_id=policy_id).first()

    if not policy:
        return jsonify({"status": "error", "success": False, "error": "Policy not found."}), 404

    try:
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
    except ValueError:
        return jsonify({"status": "error", "success": False, "error": "Invalid date format. Expected YYYY-MM-DD."}), 400

    # Business days
    business_days = calculate_business_days(start_date, end_date, employee.country)

    # Holidays in range
    holidays = COUNTRY_HOLIDAYS_2026.get(employee.country, set())
    holidays_in_span = [h.isoformat() for h in holidays if start_date <= h <= end_date]

    # Available balance
    available_balance, _ = get_available_leave_balance(employee.employee_id, policy.leave_policy_id, start_date.year)

    # Evaluate preliminary eligibility
    dummy_req = {
        "employee_id": employee.employee_id,
        "leave_policy_id": policy.leave_policy_id,
        "start_date": start_date,
        "end_date": end_date,
        "status": "submitted",
    }
    eval_res = evaluate_leave_request(dummy_req)

    return jsonify(
        {
            "status": "ok",
            "success": True,
            "business_days": float(business_days),
            "available_balance": float(available_balance),
            "holidays_in_span": holidays_in_span,
            "max_consecutive_days": float(policy.max_consecutive_days) if policy.max_consecutive_days is not None else None,
            "is_eligible": eval_res.get("decision") != "REJECT",
            "decision": eval_res.get("decision"),
            "reason_code": eval_res.get("reason_code"),
            "reason": eval_res.get("reason"),
        }
    )


@api_bp.route("/cost-center-budget/<cost_center_id>")
def cost_center_budget(cost_center_id: str):
    """Retrieve real-time budget utilization metrics for a cost center."""
    cc = CostCenter.query.filter_by(cost_center_id=cost_center_id).first_or_404()
    util = get_cost_center_budget_utilization(cost_center_id, 2026)
    currency = COUNTRY_CURRENCIES.get(cc.country, "USD")

    return jsonify(
        {
            "status": "ok",
            "success": True,
            "cost_center_id": cc.cost_center_id,
            "code": cc.code,
            "name": cc.name,
            "country": cc.country,
            "currency": currency,
            "annual_budget": float(util["annual_budget"]),
            "budget_used": float(util["budget_used"]),
            "budget_remaining": float(util["budget_remaining"]),
            "utilization_percentage": float(util["utilization_percentage"]),
            "approved_claims_count": util["approved_claims_count"],
        }
    )


@api_bp.route("/policies-for-country/<country>")
def policies_for_country(country: str):
    """List statutory leave and expense policies for a specific country."""
    leave_pols = LeavePolicy.query.filter_by(country=country, is_active=True).all()
    expense_pols = ExpensePolicy.query.filter_by(country=country, is_active=True).all()

    pols_summary = [
        {
            "policy_id": p.leave_policy_id,
            "name": p.name,
            "type": p.leave_type,
            "country": p.country,
            "allowance": float(p.annual_allowance_days),
            "max_consecutive": float(p.max_consecutive_days) if p.max_consecutive_days is not None else None,
        }
        for p in leave_pols
    ]

    return jsonify(
        {
            "status": "ok",
            "success": True,
            "country": country,
            "currency": COUNTRY_CURRENCIES.get(country, "USD"),
            "policies": pols_summary,
            "leave_policies": pols_summary,
            "expense_policies": [
                {
                    "policy_id": p.expense_policy_id,
                    "name": p.name,
                    "category": p.category,
                    "country": p.country,
                    "max_amount": float(p.max_amount),
                    "receipt_required_above": float(p.receipt_required_above) if p.receipt_required_above is not None else None,
                }
                for p in expense_pols
            ],
        }
    )
