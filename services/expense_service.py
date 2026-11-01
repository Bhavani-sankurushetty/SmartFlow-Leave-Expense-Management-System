"""Expense claim routing, policy checks, receipt validation, and totals reconciliation.

Phase 6: Expense Business Logic.
Phase 9: Budget-Aware Approval Workflow Integration and Reimbursement Lifecycle.
Evaluates expense claims against employee tenure, expense policies, cost centers,
annual budget limits, currency consistency, line item counts, positive amounts,
totals reconciliation, policy limits, mandatory receipt thresholds, and cross-claim duplicate receipt detection.
Does NOT modify database records or create approval steps automatically.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from generator.constants import COUNTRY_CURRENCIES
from models import CostCenter, Employee, ExpenseClaim, ExpenseLine, ExpensePolicy

TWO_PLACES = Decimal("0.01")


def money(val) -> Decimal:
    """Format and quantize value to two decimal places."""
    return Decimal(str(val)).quantize(TWO_PLACES)


def extract_receipt_metadata(line: ExpenseLine | dict) -> tuple[str | None, str | None]:
    """Extract receipt filename and SHA-256 hash from an expense line.

    Supports:
    1. Direct attributes/keys: 'receipt_filename' / '_receipt_filename' and 'receipt_hash' / '_receipt_hash'.
    2. Embedded text inside line description formatted as:
       '[Receipt: <filename> | SHA256: <hash>]'.

    Returns (filename, hash) or (None, None) if not present.
    """
    if isinstance(line, dict):
        fn = line.get("receipt_filename") or line.get("_receipt_filename")
        h = line.get("receipt_hash") or line.get("_receipt_hash")
        desc = line.get("description") or ""
    else:
        fn = getattr(line, "receipt_filename", None) or getattr(line, "_receipt_filename", None)
        h = getattr(line, "receipt_hash", None) or getattr(line, "_receipt_hash", None)
        desc = getattr(line, "description", None) or ""

    if fn and h:
        return str(fn), str(h)

    match = re.search(r"\[Receipt:\s*([^\|\s]+)\s*\|\s*SHA256:\s*([a-fA-F0-9]+)\]", desc)
    if match:
        extracted_fn, extracted_hash = match.group(1), match.group(2)
        return fn or extracted_fn, h or extracted_hash

    return fn, h


def get_cost_center_budget_utilization(
    cost_center_id: str,
    year: int,
    exclude_claim_id: str | None = None,
) -> dict[str, Any]:
    """Calculate annual budget utilization for a cost center in a given calendar year.

    Requirements:
    - Utilized budget is computed from finalized approved claims without double-counting reimbursements.
    - Uses the cost center country's corresponding currency from COUNTRY_CURRENCIES.
    - Never aggregates amounts across different currencies without an explicit conversion model.
    - Remaining budget = annual_budget - utilized_budget.
    - Utilization percentage = (utilized_budget / annual_budget) * 100.
    - Non-mutating read-only calculation.
    """
    cost_center = CostCenter.query.filter_by(cost_center_id=cost_center_id).first()
    if not cost_center:
        return {
            "cost_center_id": cost_center_id,
            "cost_center_code": "",
            "cost_center_name": "",
            "country": "",
            "year": year,
            "currency": "USD",
            "annual_budget": Decimal("0.00"),
            "budget_used": Decimal("0.00"),
            "budget_remaining": Decimal("0.00"),
            "utilization_percentage": 0.0,
            "approved_claims_count": 0,
            "is_exhausted": True,
            "error": f"Cost center '{cost_center_id}' not found.",
        }

    currency = COUNTRY_CURRENCIES.get(cost_center.country, "USD")
    annual_budget = money(cost_center.annual_budget)

    start_of_year = date(year, 1, 1)
    end_of_year = date(year, 12, 31)

    query = ExpenseClaim.query.filter(
        ExpenseClaim.cost_center_id == cost_center_id,
        ExpenseClaim.status == "approved",
        ExpenseClaim.claim_date >= start_of_year,
        ExpenseClaim.claim_date <= end_of_year,
    )
    if exclude_claim_id:
        query = query.filter(ExpenseClaim.expense_claim_id != exclude_claim_id)

    approved_claims = query.all()

    # Currency validation: ensure all approved claims match cost center currency
    for claim in approved_claims:
        if claim.currency != currency:
            raise ValueError(
                f"Cost center '{cost_center_id}' has approved claim '{claim.expense_claim_id}' denominated in '{claim.currency}', "
                f"which differs from cost center currency '{currency}'. Aggregation across currencies without explicit conversion is prohibited."
            )

    utilized_budget = money(sum(Decimal(str(c.total_amount)) for c in approved_claims))
    remaining_budget = money(annual_budget - utilized_budget)

    utilization_pct = 0.0
    if annual_budget > Decimal("0.00"):
        utilization_pct = round(float((utilized_budget / annual_budget) * Decimal("100.0")), 2)

    return {
        "cost_center_id": cost_center.cost_center_id,
        "cost_center_code": cost_center.code,
        "cost_center_name": cost_center.name,
        "country": cost_center.country,
        "year": year,
        "currency": currency,
        "annual_budget": annual_budget,
        "budget_used": utilized_budget,
        "budget_remaining": remaining_budget,
        "utilization_percentage": utilization_pct,
        "approved_claims_count": len(approved_claims),
        "is_exhausted": remaining_budget <= Decimal("0.00"),
        "error": None,
    }


def evaluate_expense_claim(claim: ExpenseClaim | dict | str, auto_approve: bool = False) -> dict:
    """Evaluate an expense claim against policy limits, categories, lines, receipts, and cost center budgets.

    Accepts:
    - An ExpenseClaim SQLAlchemy model instance.
    - A dictionary representing a claim and its lines.
    - An expense_claim_id string to load from the database.

    Returns a structured decision dictionary:
    - claim_id: Identifier of the claim.
    - employee_id: Identifier of the employee.
    - decision: 'REJECT' or 'NEEDS_REVIEW' (eligibility check does not grant final approval).
    - reason_code: Programmatic code explaining the decision.
    - reason: Human-readable explanation.
    - claim_amount: Total claim amount (Decimal).
    - policy_limit: Applicable policy limit (Decimal).
    - country: Country of the claim.
    - category: Category of the claim.
    - evaluated_at: UTC timestamp of evaluation.
    - budget_used: Annual budget already utilized by approved claims in cost center (Decimal).
    - budget_remaining: Remaining annual budget available in cost center (Decimal).
    - utilization_percentage: Utilization percentage of the cost center budget (float).
    - requested_amount: Requested claim amount (Decimal).
    - annual_budget: Total annual budget of the cost center (Decimal).
    - currency: Currency denomination.

    Behavior:
    - All eligible claims return 'NEEDS_REVIEW' with reason_code 'ELIGIBLE_FOR_APPROVAL',
      preserving the requirement for approval workflow execution.
    - Budget overruns return 'REJECT' with reason_code 'BUDGET_EXCEEDED' or 'BUDGET_EXHAUSTED'.
    - Safe to run repeatedly; performs zero database updates.
    """
    evaluated_at = datetime.now(timezone.utc)

    # 1. Normalize input
    claim_currency = None
    if isinstance(claim, str):
        claim_obj = ExpenseClaim.query.filter_by(expense_claim_id=claim).first()
        if not claim_obj:
            return {
                "claim_id": claim,
                "employee_id": "",
                "decision": "REJECT",
                "reason_code": "CLAIM_NOT_FOUND",
                "reason": f"Expense claim '{claim}' was not found in the database.",
                "claim_amount": Decimal("0.00"),
                "policy_limit": Decimal("0.00"),
                "country": "",
                "category": "",
                "evaluated_at": evaluated_at,
                "budget_used": Decimal("0.00"),
                "budget_remaining": Decimal("0.00"),
                "utilization_percentage": 0.0,
                "requested_amount": Decimal("0.00"),
                "annual_budget": Decimal("0.00"),
                "currency": "USD",
            }
        claim_id = claim_obj.expense_claim_id
        emp_id = claim_obj.employee_id
        pol_id = claim_obj.expense_policy_id
        cc_id = claim_obj.cost_center_id
        claim_dt = claim_obj.claim_date
        country = claim_obj.country
        claim_currency = claim_obj.currency
        total_amt = Decimal(str(claim_obj.total_amount))
        status = claim_obj.status
        lines = list(claim_obj.expense_lines)
    elif isinstance(claim, dict):
        claim_id = claim.get("claim_id") or claim.get("expense_claim_id")
        emp_id = claim.get("employee_id")
        pol_id = claim.get("expense_policy_id") or claim.get("policy_id")
        cc_id = claim.get("cost_center_id")
        claim_dt = claim.get("claim_date")
        country = claim.get("country")
        claim_currency = claim.get("currency")
        total_amt = Decimal(str(claim.get("total_amount") or claim.get("claim_amount") or "0.00"))
        status = claim.get("status", "submitted")
        lines = claim.get("lines") or claim.get("expense_lines") or []
        if not lines and claim_id:
            db_claim = ExpenseClaim.query.filter_by(expense_claim_id=claim_id).first()
            if db_claim:
                lines = list(db_claim.expense_lines)
                if not claim_currency:
                    claim_currency = db_claim.currency
    else:
        claim_id = getattr(claim, "expense_claim_id", None) or getattr(claim, "claim_id", None)
        emp_id = getattr(claim, "employee_id", None)
        pol_id = getattr(claim, "expense_policy_id", None)
        cc_id = getattr(claim, "cost_center_id", None)
        claim_dt = getattr(claim, "claim_date", None)
        country = getattr(claim, "country", None)
        claim_currency = getattr(claim, "currency", None)
        total_amt = Decimal(str(getattr(claim, "total_amount", "0.00")))
        status = getattr(claim, "status", "submitted")
        lines = list(getattr(claim, "expense_lines", []))

    def make_decision(
        decision: str,
        reason_code: str,
        reason: str,
        policy_limit: Decimal = Decimal("0.00"),
        cat: str = "",
        budget_info: dict | None = None,
    ) -> dict:
        expected_curr = budget_info["currency"] if budget_info else COUNTRY_CURRENCIES.get(str(country or ""), "USD")
        return {
            "claim_id": str(claim_id or ""),
            "employee_id": str(emp_id or ""),
            "decision": decision,
            "reason_code": reason_code,
            "reason": reason,
            "claim_amount": money(total_amt),
            "policy_limit": money(policy_limit),
            "country": str(country or ""),
            "category": str(cat or ""),
            "evaluated_at": evaluated_at,
            "budget_used": budget_info["budget_used"] if budget_info else Decimal("0.00"),
            "budget_remaining": budget_info["budget_remaining"] if budget_info else Decimal("0.00"),
            "utilization_percentage": budget_info["utilization_percentage"] if budget_info else 0.0,
            "requested_amount": money(total_amt),
            "annual_budget": budget_info["annual_budget"] if budget_info else Decimal("0.00"),
            "currency": expected_curr,
        }

    # 2. Employee existence check
    employee = Employee.query.filter_by(employee_id=emp_id).first() if emp_id else None
    if not employee:
        return make_decision("REJECT", "EMPLOYEE_NOT_FOUND", f"Employee '{emp_id}' does not exist.")

    if not country:
        country = employee.country

    # 3. Employment tenure validity
    if not employee.hire_date:
        return make_decision("REJECT", "INVALID_EMPLOYMENT_DATE", "Employee hire date is missing or invalid.")

    # 4. Expense policy existence check
    policy = ExpensePolicy.query.filter_by(expense_policy_id=pol_id).first() if pol_id else None
    if not policy or not policy.is_active:
        return make_decision("REJECT", "POLICY_NOT_FOUND", f"Expense policy '{pol_id}' does not exist or is inactive.")

    pol_limit = Decimal(str(policy.max_amount))
    category = policy.category
    receipt_threshold = Decimal(str(policy.receipt_required_above)) if policy.receipt_required_above is not None else None

    # 5. Country matching: Employee vs Policy vs Claim
    if policy.country != employee.country or policy.country != country:
        return make_decision(
            "REJECT",
            "COUNTRY_POLICY_MISMATCH",
            f"Policy country ({policy.country}) does not match employee country ({employee.country}) or claim country ({country}).",
            policy_limit=pol_limit,
            cat=category,
        )

    # 6. Cost center validation and country/currency consistency
    cost_center = CostCenter.query.filter_by(cost_center_id=cc_id).first() if cc_id else None
    if not cost_center or not cost_center.is_active:
        return make_decision(
            "REJECT",
            "COST_CENTER_NOT_FOUND",
            f"Cost center '{cc_id}' does not exist or is inactive.",
            policy_limit=pol_limit,
            cat=category,
        )

    if cost_center.country != employee.country or cost_center.country != country:
        return make_decision(
            "REJECT",
            "COUNTRY_COST_CENTER_MISMATCH",
            f"Cost center country ({cost_center.country}) does not match employee country ({employee.country}) or claim country ({country}).",
            policy_limit=pol_limit,
            cat=category,
        )

    expected_currency = COUNTRY_CURRENCIES.get(cost_center.country, "USD")
    if claim_currency and claim_currency != expected_currency:
        return make_decision(
            "REJECT",
            "CURRENCY_MISMATCH",
            f"Claim currency ({claim_currency}) does not match expected cost center currency ({expected_currency}) for country '{cost_center.country}'.",
            policy_limit=pol_limit,
            cat=category,
        )

    # 7. Line count check (between 1 and 10 lines)
    if not lines or not (1 <= len(lines) <= 10):
        return make_decision(
            "REJECT",
            "INVALID_LINE_COUNT",
            f"Claim must contain between 1 and 10 expense lines, found {len(lines)}.",
            policy_limit=pol_limit,
            cat=category,
        )

    # 8. Claim date within employment tenure
    if claim_dt:
        if claim_dt < employee.hire_date:
            return make_decision(
                "REJECT",
                "EXPENSE_DATE_OUTSIDE_EMPLOYMENT",
                f"Claim date ({claim_dt}) is before employee hire date ({employee.hire_date}).",
                policy_limit=pol_limit,
                cat=category,
            )
        if employee.termination_date and claim_dt > employee.termination_date:
            return make_decision(
                "REJECT",
                "EXPENSE_DATE_OUTSIDE_EMPLOYMENT",
                f"Claim date ({claim_dt}) is after employee termination date ({employee.termination_date}).",
                policy_limit=pol_limit,
                cat=category,
            )

    # 9. Iterate lines and validate dates, amounts, categories, receipts
    calculated_total = Decimal("0.00")
    seen_hashes_in_claim = set()

    for idx, line in enumerate(lines, start=1):
        if isinstance(line, dict):
            line_amt = Decimal(str(line.get("amount") or "0.00"))
            line_dt = line.get("expense_date")
            line_cat = line.get("category")
        else:
            line_amt = Decimal(str(getattr(line, "amount", "0.00")))
            line_dt = getattr(line, "expense_date", None)
            line_cat = getattr(line, "category", None)

        # 9a. Category check
        if line_cat and line_cat != policy.category:
            return make_decision(
                "REJECT",
                "CATEGORY_POLICY_MISMATCH",
                f"Expense line {idx} category ({line_cat}) does not match policy category ({policy.category}).",
                policy_limit=pol_limit,
                cat=category,
            )

        # 9b. Positive amount
        if line_amt <= Decimal("0.00"):
            return make_decision(
                "REJECT",
                "INVALID_EXPENSE_AMOUNT",
                f"Expense line {idx} amount ({line_amt}) must be greater than zero.",
                policy_limit=pol_limit,
                cat=category,
            )

        # 9c. Line date validity
        if not line_dt or (claim_dt and line_dt > claim_dt):
            return make_decision(
                "REJECT",
                "INVALID_EXPENSE_DATE",
                f"Expense line {idx} date ({line_dt}) is invalid or after claim date ({claim_dt}).",
                policy_limit=pol_limit,
                cat=category,
            )

        if line_dt < employee.hire_date:
            return make_decision(
                "REJECT",
                "EXPENSE_DATE_OUTSIDE_EMPLOYMENT",
                f"Expense line {idx} date ({line_dt}) is before employee hire date ({employee.hire_date}).",
                policy_limit=pol_limit,
                cat=category,
            )

        if employee.termination_date and line_dt > employee.termination_date:
            return make_decision(
                "REJECT",
                "EXPENSE_DATE_OUTSIDE_EMPLOYMENT",
                f"Expense line {idx} date ({line_dt}) is after employee termination date ({employee.termination_date}).",
                policy_limit=pol_limit,
                cat=category,
            )

        # 9d. Receipt required check
        rec_fn, rec_h = extract_receipt_metadata(line)
        if receipt_threshold is not None and line_amt >= receipt_threshold:
            if not rec_fn or not rec_h:
                return make_decision(
                    "REJECT",
                    "RECEIPT_REQUIRED",
                    f"Expense line {idx} amount ({line_amt}) requires a receipt (threshold: {receipt_threshold}), but no receipt metadata was found.",
                    policy_limit=pol_limit,
                    cat=category,
                )

        # 9e. Duplicate receipt hash check
        if rec_h:
            if rec_h in seen_hashes_in_claim:
                return make_decision(
                    "REJECT",
                    "DUPLICATE_RECEIPT",
                    f"Duplicate receipt hash '{rec_h}' detected within the same claim (line {idx}).",
                    policy_limit=pol_limit,
                    cat=category,
                )
            seen_hashes_in_claim.add(rec_h)

            other_line = ExpenseLine.query.filter(
                ExpenseLine.expense_claim_id != claim_id,
                ExpenseLine.description.like(f"%{rec_h}%"),
            ).first()
            if other_line:
                return make_decision(
                    "REJECT",
                    "DUPLICATE_RECEIPT",
                    f"Duplicate receipt hash '{rec_h}' detected; already submitted in claim '{other_line.expense_claim_id}'.",
                    policy_limit=pol_limit,
                    cat=category,
                )

        calculated_total += line_amt

    # 10. Claim total reconciliation
    if money(total_amt) != money(calculated_total):
        return make_decision(
            "REJECT",
            "CLAIM_TOTAL_MISMATCH",
            f"Claim total ({total_amt}) does not match the sum of its expense lines ({calculated_total}).",
            policy_limit=pol_limit,
            cat=category,
        )

    # 11. Policy limit check
    if calculated_total > pol_limit:
        return make_decision(
            "REJECT",
            "POLICY_LIMIT_EXCEEDED",
            f"Claim total amount ({calculated_total}) exceeds policy limit ({pol_limit}).",
            policy_limit=pol_limit,
            cat=category,
        )

    # 12. Cost Center Budget Validation (Phase 9)
    claim_year = claim_dt.year if claim_dt else 2026
    budget_info = get_cost_center_budget_utilization(cc_id, claim_year, exclude_claim_id=claim_id)

    if budget_info["is_exhausted"]:
        return make_decision(
            "REJECT",
            "BUDGET_EXHAUSTED",
            f"Cost center '{cost_center.code}' annual budget of {budget_info['annual_budget']} {budget_info['currency']} for {claim_year} is completely exhausted (utilized: {budget_info['budget_used']} {budget_info['currency']}).",
            policy_limit=pol_limit,
            cat=category,
            budget_info=budget_info,
        )

    if calculated_total > budget_info["budget_remaining"]:
        return make_decision(
            "REJECT",
            "BUDGET_EXCEEDED",
            f"Claim amount ({money(calculated_total)} {budget_info['currency']}) exceeds available remaining budget ({budget_info['budget_remaining']} {budget_info['currency']} of {budget_info['annual_budget']} {budget_info['currency']}) for cost center '{cost_center.code}'.",
            policy_limit=pol_limit,
            cat=category,
            budget_info=budget_info,
        )

    # 13. Decision: eligibility passed
    decision = "NEEDS_REVIEW"
    reason = (
        f"Expense claim meets all policy, receipt, and cost center budget requirements "
        f"({money(calculated_total)} {budget_info['currency']} within policy limit {pol_limit} and remaining budget {budget_info['budget_remaining']} {budget_info['currency']}), "
        f"pending approval workflow."
    )

    return make_decision(
        decision,
        "ELIGIBLE_FOR_APPROVAL",
        reason,
        policy_limit=pol_limit,
        cat=category,
        budget_info=budget_info,
    )
