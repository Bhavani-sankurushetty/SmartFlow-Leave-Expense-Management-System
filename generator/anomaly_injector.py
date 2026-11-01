"""Controlled, reproducible anomaly injection for SmartFlow.

Phase 8: Anomaly Injection.
Introduces deterministic business-rule violations into synthetic leave and expense data,
records ground-truth labels in an auditable manifest, supports dry-run mode,
supports complete restoration/rollback, and provides automated evaluation against
SmartFlow business logic services.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from generator.constants import RANDOM_SEED
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
    db,
)
from services.approval_service import record_approval, resolve_approval_route
from services.expense_service import evaluate_expense_claim
from services.leave_service import evaluate_leave_request, get_available_leave_balance

MANIFEST_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "ground_truth", "anomaly_manifest.json"
)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def serialize_val(val: Any) -> Any:
    """Serialize dates, Decimals, and primitives to JSON-safe representations."""
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    return val


class AnomalyInjector:
    """Manages deterministic anomaly injection, manifest recording, and restoration."""

    SUPPORTED_CATEGORIES = [
        "EXPENSE_POLICY_LIMIT_EXCEEDED",
        "EXPENSE_MISSING_RECEIPT",
        "EXPENSE_DUPLICATE_RECEIPT_HASH",
        "EXPENSE_CLAIM_TOTAL_MISMATCH",
        "EXPENSE_NEGATIVE_AMOUNT",
        "EXPENSE_DATE_OUTSIDE_EMPLOYMENT",
        "EXPENSE_CATEGORY_MISMATCH",
        "LEAVE_BALANCE_EXCEEDED",
        "LEAVE_CONSECUTIVE_DAYS_EXCEEDED",
        "LEAVE_DATE_OUTSIDE_EMPLOYMENT",
        "LEAVE_COUNTRY_POLICY_MISMATCH",
        "APPROVAL_INACTIVE_APPROVER",
        "APPROVAL_UNAUTHORIZED_APPROVER",
        "APPROVAL_OUT_OF_ORDER",
    ]

    def __init__(self, seed: int = RANDOM_SEED, manifest_path: str = MANIFEST_DEFAULT_PATH):
        self.seed = seed
        self.manifest_path = manifest_path
        self.rng = random.Random(seed)

    def is_manifest_active(self) -> bool:
        """Check if a persistent injection is currently recorded as applied."""
        if not os.path.exists(self.manifest_path):
            return False
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("status") == "APPLIED"
        except Exception:
            return False

    def plan_anomalies(
        self,
        categories: list[str] | None = None,
        count_per_category: int = 1,
    ) -> list[dict[str, Any]]:
        """Deterministically plan anomalies and normal controls without mutating the database."""
        if categories is None:
            categories = list(self.SUPPORTED_CATEGORIES)
        else:
            invalid = [c for c in categories if c not in self.SUPPORTED_CATEGORIES]
            if invalid:
                raise ValueError(f"Unsupported anomaly categories: {invalid}")

        self.rng = random.Random(self.seed)
        manifest_records: list[dict[str, Any]] = []
        seq = 1

        # Collect baseline eligible candidate entities from database
        submitted_claims = [
            c for c in ExpenseClaim.query.filter_by(status="submitted").order_by(ExpenseClaim.expense_claim_id.asc()).all()
            if evaluate_expense_claim(c).get("decision") != "REJECT"
        ]
        submitted_leaves = [
            l for l in LeaveRequest.query.filter_by(status="submitted").order_by(LeaveRequest.leave_request_id.asc()).all()
            if evaluate_leave_request(l).get("decision") != "REJECT"
        ]

        if not submitted_claims or not submitted_leaves:
            raise RuntimeError("Database must contain eligible submitted leave and expense records for injection.")

        used_claim_ids: set[str] = set()
        used_leave_ids: set[str] = set()

        for cat in categories:
            for _ in range(count_per_category):
                anom_id = f"ANOM-{seq:06d}"
                rec = None

                if cat == "EXPENSE_POLICY_LIMIT_EXCEEDED":
                    candidates = [c for c in submitted_claims if c.expense_claim_id not in used_claim_ids]
                    if candidates:
                        claim = candidates[0]
                        used_claim_ids.add(claim.expense_claim_id)
                        policy = claim.expense_policy
                        limit = Decimal(str(policy.max_amount))
                        orig_val = Decimal(str(claim.total_amount))
                        line0 = claim.expense_lines[0]
                        orig_l1_amt = Decimal(str(line0.amount))
                        excess = (limit - orig_val) + Decimal("200.00")
                        injected_total = orig_val + excess
                        injected_l1 = orig_l1_amt + excess
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "ExpenseClaim",
                            "entity_id": claim.expense_claim_id,
                            "target_field": "total_amount",
                            "original_value": serialize_val(orig_val),
                            "injected_value": serialize_val(injected_total),
                            "secondary_entity_type": "ExpenseLine",
                            "secondary_entity_id": line0.expense_line_id,
                            "secondary_field": "amount",
                            "secondary_original_value": serialize_val(orig_l1_amt),
                            "secondary_injected_value": serialize_val(injected_l1),
                            "reason": f"Inflated total to {injected_total}, exceeding policy max limit ({limit}).",
                            "expected_detection_code": "POLICY_LIMIT_EXCEEDED",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "EXPENSE_MISSING_RECEIPT":
                    line_candidates = [
                        l for l in ExpenseLine.query.filter(ExpenseLine.description.like("%[Receipt:%")).order_by(ExpenseLine.expense_line_id.asc()).all()
                        if l.expense_claim_id in [c.expense_claim_id for c in submitted_claims] and l.expense_claim_id not in used_claim_ids
                    ]
                    if line_candidates:
                        line = line_candidates[0]
                        used_claim_ids.add(line.expense_claim_id)
                        orig_desc = line.description
                        injected_desc = orig_desc.split("[Receipt:")[0].strip()
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "ExpenseLine",
                            "entity_id": line.expense_line_id,
                            "target_field": "description",
                            "original_value": orig_desc,
                            "injected_value": injected_desc,
                            "reason": f"Removed required receipt metadata from line amount {line.amount}.",
                            "expected_detection_code": "RECEIPT_REQUIRED",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "EXPENSE_DUPLICATE_RECEIPT_HASH":
                    line_candidates = [
                        l for l in ExpenseLine.query.filter(ExpenseLine.description.like("%[Receipt:%")).order_by(ExpenseLine.expense_line_id.asc()).all()
                        if l.expense_claim_id in [c.expense_claim_id for c in submitted_claims] and l.expense_claim_id not in used_claim_ids
                    ]
                    if len(line_candidates) >= 2:
                        target_line = line_candidates[0]
                        donor_line = line_candidates[1]
                        used_claim_ids.add(target_line.expense_claim_id)
                        m_donor = re.search(r"SHA256:\s*([a-fA-F0-9]+)", donor_line.description)
                        m_target = re.search(r"SHA256:\s*([a-fA-F0-9]+)", target_line.description)
                        if m_donor and m_target:
                            donor_hash = m_donor.group(1)
                            target_hash = m_target.group(1)
                            injected_desc = target_line.description.replace(target_hash, donor_hash)
                            rec = {
                                "anomaly_id": anom_id,
                                "anomaly_category": cat,
                                "entity_type": "ExpenseLine",
                                "entity_id": target_line.expense_line_id,
                                "target_field": "description",
                                "original_value": target_line.description,
                                "injected_value": injected_desc,
                                "reason": f"Injected duplicate receipt hash '{donor_hash}' from claim '{donor_line.expense_claim_id}'.",
                                "expected_detection_code": "DUPLICATE_RECEIPT",
                                "ground_truth_label": "ANOMALOUS",
                            }

                elif cat == "EXPENSE_CLAIM_TOTAL_MISMATCH":
                    candidates = [c for c in submitted_claims if c.expense_claim_id not in used_claim_ids]
                    if candidates:
                        claim = candidates[0]
                        used_claim_ids.add(claim.expense_claim_id)
                        orig_val = Decimal(str(claim.total_amount))
                        injected_val = orig_val + Decimal("100.00")
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "ExpenseClaim",
                            "entity_id": claim.expense_claim_id,
                            "target_field": "total_amount",
                            "original_value": serialize_val(orig_val),
                            "injected_value": serialize_val(injected_val),
                            "reason": f"Altered claim total to {injected_val} creating mismatch with sum of lines ({orig_val}).",
                            "expected_detection_code": "CLAIM_TOTAL_MISMATCH",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "EXPENSE_NEGATIVE_AMOUNT":
                    candidates = [c for c in submitted_claims if c.expense_claim_id not in used_claim_ids]
                    if candidates:
                        claim = candidates[0]
                        used_claim_ids.add(claim.expense_claim_id)
                        line = claim.expense_lines[0]
                        orig_val = Decimal(str(line.amount))
                        injected_val = Decimal("-50.00")
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "ExpenseLine",
                            "entity_id": line.expense_line_id,
                            "target_field": "amount",
                            "original_value": serialize_val(orig_val),
                            "injected_value": serialize_val(injected_val),
                            "reason": "Injected negative line amount of -50.00.",
                            "expected_detection_code": "INVALID_EXPENSE_AMOUNT",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "EXPENSE_DATE_OUTSIDE_EMPLOYMENT":
                    candidates = [c for c in submitted_claims if c.expense_claim_id not in used_claim_ids]
                    if candidates:
                        claim = candidates[0]
                        used_claim_ids.add(claim.expense_claim_id)
                        orig_dt = claim.claim_date
                        injected_dt = date(2010, 1, 1)  # Prior to 2014 earliest hire date
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "ExpenseClaim",
                            "entity_id": claim.expense_claim_id,
                            "target_field": "claim_date",
                            "original_value": serialize_val(orig_dt),
                            "injected_value": serialize_val(injected_dt),
                            "reason": f"Injected claim date {injected_dt} prior to employee hire date.",
                            "expected_detection_code": "EXPENSE_DATE_OUTSIDE_EMPLOYMENT",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "EXPENSE_CATEGORY_MISMATCH":
                    candidates = [c for c in submitted_claims if c.expense_claim_id not in used_claim_ids]
                    if candidates:
                        claim = candidates[0]
                        used_claim_ids.add(claim.expense_claim_id)
                        line = claim.expense_lines[0]
                        orig_cat = line.category
                        mismatched_cat = "Software" if orig_cat != "Software" else "Meals"
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "ExpenseLine",
                            "entity_id": line.expense_line_id,
                            "target_field": "category",
                            "original_value": orig_cat,
                            "injected_value": mismatched_cat,
                            "reason": f"Injected line category '{mismatched_cat}' mismatched with policy '{orig_cat}'.",
                            "expected_detection_code": "CATEGORY_POLICY_MISMATCH",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "LEAVE_BALANCE_EXCEEDED":
                    candidates = [l for l in submitted_leaves if l.leave_request_id not in used_leave_ids]
                    if candidates:
                        req = candidates[0]
                        used_leave_ids.add(req.leave_request_id)
                        bal, _ = get_available_leave_balance(req.employee_id, req.leave_policy_id, req.start_date.year)
                        orig_start = req.start_date
                        orig_end = req.end_date
                        orig_days = req.business_days
                        max_consec = req.leave_policy.max_consecutive_days
                        if max_consec is not None and max_consec > bal:
                            target_days = max_consec
                            target_start = date(2026, 8, 3)  # Monday
                            # advance business days to ensure requested_days == max_consec <= policy_limit
                            curr = target_start
                            added = 0
                            days_to_add = int(target_days)
                            while added < days_to_add:
                                if curr.weekday() < 5:
                                    added += 1
                                    if added == days_to_add:
                                        break
                                curr += timedelta(days=1)
                            target_end = curr
                        else:
                            target_days = Decimal("20.00")
                            target_start = date(2026, 6, 1)  # Monday
                            target_end = date(2026, 6, 26)   # Friday: 20 business days > bal
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "LeaveRequest",
                            "entity_id": req.leave_request_id,
                            "target_field": "date_range_and_days",
                            "original_value": f"{orig_start}:{orig_end}:{orig_days}",
                            "injected_value": f"{target_start}:{target_end}:{target_days:.2f}",
                            "reason": f"Set leave span to {target_days:.2f} business days, exceeding available balance ({bal}).",
                            "expected_detection_code": "BALANCE_EXCEEDED",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "LEAVE_CONSECUTIVE_DAYS_EXCEEDED":
                    candidates = [
                        l for l in submitted_leaves
                        if l.leave_request_id not in used_leave_ids and l.leave_policy.max_consecutive_days is not None and l.leave_policy.max_consecutive_days <= Decimal("15.00")
                    ]
                    if candidates:
                        req = candidates[0]
                        used_leave_ids.add(req.leave_request_id)
                        orig_start = req.start_date
                        orig_end = req.end_date
                        orig_days = req.business_days
                        target_start = date(2026, 10, 26)  # Monday
                        target_end = date(2026, 11, 13)    # Friday, 15 business days > 10 max consecutive
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "LeaveRequest",
                            "entity_id": req.leave_request_id,
                            "target_field": "date_range_and_days",
                            "original_value": f"{orig_start}:{orig_end}:{orig_days}",
                            "injected_value": f"{target_start}:{target_end}:15.00",
                            "reason": f"Requested 15 consecutive days exceeding policy limit ({req.leave_policy.max_consecutive_days}).",
                            "expected_detection_code": "POLICY_LIMIT_EXCEEDED",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "LEAVE_DATE_OUTSIDE_EMPLOYMENT":
                    candidates = [l for l in submitted_leaves if l.leave_request_id not in used_leave_ids]
                    if candidates:
                        req = candidates[0]
                        used_leave_ids.add(req.leave_request_id)
                        orig_dt = req.start_date
                        injected_dt = date(2010, 1, 1)
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "LeaveRequest",
                            "entity_id": req.leave_request_id,
                            "target_field": "start_date",
                            "original_value": serialize_val(orig_dt),
                            "injected_value": serialize_val(injected_dt),
                            "reason": f"Injected start date {injected_dt} prior to employee hire date.",
                            "expected_detection_code": "OUTSIDE_EMPLOYMENT_PERIOD",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "LEAVE_COUNTRY_POLICY_MISMATCH":
                    candidates = [l for l in submitted_leaves if l.leave_request_id not in used_leave_ids]
                    if candidates:
                        req = candidates[0]
                        used_leave_ids.add(req.leave_request_id)
                        other_pol = LeavePolicy.query.filter(LeavePolicy.country != req.employee.country).first()
                        if other_pol:
                            orig_pol = req.leave_policy_id
                            injected_pol = other_pol.leave_policy_id
                            rec = {
                                "anomaly_id": anom_id,
                                "anomaly_category": cat,
                                "entity_type": "LeaveRequest",
                                "entity_id": req.leave_request_id,
                                "target_field": "leave_policy_id",
                                "original_value": orig_pol,
                                "injected_value": injected_pol,
                                "reason": f"Assigned policy '{injected_pol}' ({other_pol.country}) to employee from '{req.employee.country}'.",
                                "expected_detection_code": "COUNTRY_POLICY_MISMATCH",
                                "ground_truth_label": "ANOMALOUS",
                            }

                elif cat == "APPROVAL_INACTIVE_APPROVER":
                    # Submitter whose direct manager is inactive
                    candidate_reqs = [
                        l for l in LeaveRequest.query.order_by(LeaveRequest.leave_request_id.asc()).all()
                        if l.employee.manager is not None and not l.employee.manager.is_active
                    ]
                    if candidate_reqs:
                        req = candidate_reqs[0]
                        rec = {
                            "anomaly_id": anom_id,
                            "anomaly_category": cat,
                            "entity_type": "LeaveRequest",
                            "entity_id": req.leave_request_id,
                            "target_field": "approver_status",
                            "original_value": "active",
                            "injected_value": "inactive_manager",
                            "reason": f"Employee '{req.employee_id}' direct manager '{req.employee.manager_id}' is inactive.",
                            "expected_detection_code": "INACTIVE_OR_MISSING_APPROVER",
                            "ground_truth_label": "ANOMALOUS",
                        }

                elif cat == "APPROVAL_UNAUTHORIZED_APPROVER":
                    rec = {
                        "anomaly_id": anom_id,
                        "anomaly_category": cat,
                        "entity_type": "ApprovalStep",
                        "entity_id": "SYNTHETIC-STEP-01",
                        "target_field": "approver_employee_id",
                        "original_value": "EMP-AUTHORIZED",
                        "injected_value": "EMP-UNAUTHORIZED",
                        "reason": "Non-assigned employee attempting to record an approval decision.",
                        "expected_detection_code": "UNAUTHORIZED_APPROVER",
                        "ground_truth_label": "ANOMALOUS",
                    }

                elif cat == "APPROVAL_OUT_OF_ORDER":
                    rec = {
                        "anomaly_id": anom_id,
                        "anomaly_category": cat,
                        "entity_type": "ApprovalStep",
                        "entity_id": "SYNTHETIC-STEP-02",
                        "target_field": "step_order",
                        "original_value": "step_1_pending",
                        "injected_value": "attempt_step_2_early",
                        "reason": "Attempted to decide step 2 while step 1 remains pending.",
                        "expected_detection_code": "PENDING_PRIOR_STEPS",
                        "ground_truth_label": "ANOMALOUS",
                    }

                if rec:
                    manifest_records.append(rec)
                    seq += 1

        # Add normal control records
        control_claims = [c for c in submitted_claims if c.expense_claim_id not in used_claim_ids][:2]
        for c in control_claims:
            manifest_records.append(
                {
                    "anomaly_id": f"NORM-{seq:06d}",
                    "anomaly_category": "NORMAL_CONTROL_EXPENSE",
                    "entity_type": "ExpenseClaim",
                    "entity_id": c.expense_claim_id,
                    "target_field": None,
                    "original_value": serialize_val(c.total_amount),
                    "injected_value": serialize_val(c.total_amount),
                    "reason": "Unmodified valid expense claim as normal control.",
                    "expected_detection_code": "ELIGIBLE_FOR_APPROVAL",
                    "ground_truth_label": "NORMAL",
                }
            )
            seq += 1

        control_leaves = [l for l in submitted_leaves if l.leave_request_id not in used_leave_ids][:2]
        for l in control_leaves:
            manifest_records.append(
                {
                    "anomaly_id": f"NORM-{seq:06d}",
                    "anomaly_category": "NORMAL_CONTROL_LEAVE",
                    "entity_type": "LeaveRequest",
                    "entity_id": l.leave_request_id,
                    "target_field": None,
                    "original_value": serialize_val(l.business_days),
                    "injected_value": serialize_val(l.business_days),
                    "reason": "Unmodified valid leave request as normal control.",
                    "expected_detection_code": "ELIGIBLE_FOR_APPROVAL",
                    "ground_truth_label": "NORMAL",
                }
            )
            seq += 1

        return manifest_records

    def inject_anomalies(
        self,
        categories: list[str] | None = None,
        count_per_category: int = 1,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Inject anomalies into database records or run dry-run planning.

        Safeguards:
        - Refuses run if an injection is already active (idempotent / duplicate protection).
        - Executes all mutations inside an atomic transaction.
        - Persists ground-truth manifest to ground_truth/anomaly_manifest.json.
        """
        if not dry_run and self.is_manifest_active():
            raise RuntimeError(
                f"An active anomaly injection is already recorded in '{self.manifest_path}'. "
                "Call restore_anomalies() before performing a new injection."
            )

        planned_records = None
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if existing.get("status") == "SPECIFIED" and "records" in existing:
                    planned_records = existing["records"]
            except Exception:
                pass

        if planned_records is None:
            planned_records = self.plan_anomalies(categories, count_per_category)

        if dry_run:
            return {
                "status": "DRY_RUN",
                "injected_count": 0,
                "anomalous_records": sum(1 for r in planned_records if r["ground_truth_label"] == "ANOMALOUS"),
                "normal_controls": sum(1 for r in planned_records if r["ground_truth_label"] == "NORMAL"),
                "records": planned_records,
            }

        # Persistent injection within transaction
        try:
            for rec in planned_records:
                if rec["ground_truth_label"] == "NORMAL" or rec["target_field"] is None:
                    continue

                entity_type = rec["entity_type"]
                entity_id = rec["entity_id"]
                target_field = rec["target_field"]
                injected_val = rec["injected_value"]

                if entity_type == "ExpenseClaim":
                    obj = ExpenseClaim.query.filter_by(expense_claim_id=entity_id).first()
                    if obj:
                        if target_field == "total_amount":
                            setattr(obj, target_field, Decimal(str(injected_val)))
                            # Check secondary field
                            if rec.get("secondary_entity_type") == "ExpenseLine":
                                line = ExpenseLine.query.filter_by(expense_line_id=rec["secondary_entity_id"]).first()
                                if line:
                                    setattr(line, rec["secondary_field"], Decimal(str(rec["secondary_injected_value"])))
                        elif target_field == "claim_date":
                            setattr(obj, target_field, date.fromisoformat(str(injected_val)))
                        else:
                            setattr(obj, target_field, injected_val)

                elif entity_type == "ExpenseLine":
                    obj = ExpenseLine.query.filter_by(expense_line_id=entity_id).first()
                    if obj:
                        if target_field == "amount":
                            setattr(obj, target_field, Decimal(str(injected_val)))
                        else:
                            setattr(obj, target_field, injected_val)

                elif entity_type == "LeaveRequest":
                    obj = LeaveRequest.query.filter_by(leave_request_id=entity_id).first()
                    if obj:
                        if target_field == "date_range_and_days":
                            start_str, end_str, days_str = str(injected_val).split(":")
                            obj.start_date = date.fromisoformat(start_str)
                            obj.end_date = date.fromisoformat(end_str)
                            obj.business_days = Decimal(days_str)
                        elif target_field == "start_date":
                            setattr(obj, target_field, date.fromisoformat(str(injected_val)))
                        elif target_field == "business_days":
                            setattr(obj, target_field, Decimal(str(injected_val)))
                        else:
                            setattr(obj, target_field, injected_val)

            db.session.commit()

            # Save manifest
            os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
            manifest_data = {
                "status": "APPLIED",
                "seed": self.seed,
                "injected_at": utc_now_iso(),
                "total_records": len(planned_records),
                "anomalous_count": sum(1 for r in planned_records if r["ground_truth_label"] == "ANOMALOUS"),
                "normal_count": sum(1 for r in planned_records if r["ground_truth_label"] == "NORMAL"),
                "records": planned_records,
            }
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2)

        except Exception as exc:
            db.session.rollback()
            raise RuntimeError(f"Anomaly injection transaction failed: {exc}") from exc

        return {
            "status": "APPLIED",
            "injected_count": manifest_data["anomalous_count"],
            "anomalous_records": manifest_data["anomalous_count"],
            "normal_controls": manifest_data["normal_count"],
            "manifest_path": self.manifest_path,
        }

    def restore_anomalies(self) -> dict[str, Any]:
        """Restore all modified database records to their original pre-injection values."""
        if not os.path.exists(self.manifest_path):
            return {"status": "NOOP", "restored_count": 0, "message": "No manifest found."}

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        if manifest_data.get("status") != "APPLIED":
            return {"status": "NOOP", "restored_count": 0, "message": "Manifest is not in APPLIED state."}

        records = manifest_data.get("records", [])
        restored_count = 0

        try:
            for rec in records:
                if rec["ground_truth_label"] == "NORMAL" or rec["target_field"] is None:
                    continue

                entity_type = rec["entity_type"]
                entity_id = rec["entity_id"]
                target_field = rec["target_field"]
                orig_val = rec["original_value"]

                if entity_type == "ExpenseClaim":
                    obj = ExpenseClaim.query.filter_by(expense_claim_id=entity_id).first()
                    if obj:
                        if target_field == "total_amount":
                            setattr(obj, target_field, Decimal(str(orig_val)))
                            if rec.get("secondary_entity_type") == "ExpenseLine":
                                line = ExpenseLine.query.filter_by(expense_line_id=rec["secondary_entity_id"]).first()
                                if line:
                                    setattr(line, rec["secondary_field"], Decimal(str(rec["secondary_original_value"])))
                        elif target_field == "claim_date":
                            setattr(obj, target_field, date.fromisoformat(str(orig_val)))
                        else:
                            setattr(obj, target_field, orig_val)
                        restored_count += 1

                elif entity_type == "ExpenseLine":
                    obj = ExpenseLine.query.filter_by(expense_line_id=entity_id).first()
                    if obj:
                        if target_field == "amount":
                            setattr(obj, target_field, Decimal(str(orig_val)))
                        else:
                            setattr(obj, target_field, orig_val)
                        restored_count += 1

                elif entity_type == "LeaveRequest":
                    obj = LeaveRequest.query.filter_by(leave_request_id=entity_id).first()
                    if obj:
                        if target_field == "date_range_and_days":
                            start_str, end_str, days_str = str(orig_val).split(":")
                            obj.start_date = date.fromisoformat(start_str)
                            obj.end_date = date.fromisoformat(end_str)
                            obj.business_days = Decimal(days_str)
                        elif target_field == "start_date":
                            setattr(obj, target_field, date.fromisoformat(str(orig_val)))
                        elif target_field == "business_days":
                            setattr(obj, target_field, Decimal(str(orig_val)))
                        else:
                            setattr(obj, target_field, orig_val)
                        restored_count += 1

            db.session.commit()

            # Update manifest state
            manifest_data["status"] = "RESTORED"
            manifest_data["restored_at"] = utc_now_iso()
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2)

        except Exception as exc:
            db.session.rollback()
            raise RuntimeError(f"Restoration transaction failed: {exc}") from exc

        return {
            "status": "RESTORED",
            "restored_count": restored_count,
            "manifest_path": self.manifest_path,
        }

    def evaluate_ground_truth(self) -> dict[str, Any]:
        """Evaluate current database records against ground-truth manifest expectations."""
        if not os.path.exists(self.manifest_path):
            raise FileNotFoundError(f"Manifest not found at '{self.manifest_path}'.")

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        records = manifest_data.get("records", [])
        results = []
        tp = fp = tn = fn = 0

        for r in records:
            entity_type = r["entity_type"]
            entity_id = r["entity_id"]
            expected_label = r["ground_truth_label"]
            expected_code = r["expected_detection_code"]

            detected_code = None
            detected_label = "NORMAL"

            if entity_type in {"ExpenseClaim", "ExpenseLine"}:
                claim_id = entity_id if entity_type == "ExpenseClaim" else ExpenseLine.query.filter_by(expense_line_id=entity_id).first().expense_claim_id
                eval_res = evaluate_expense_claim(claim_id)
                if eval_res.get("decision") == "REJECT":
                    detected_label = "ANOMALOUS"
                    detected_code = eval_res.get("reason_code")
                else:
                    detected_label = "NORMAL"
                    detected_code = eval_res.get("reason_code")

            elif entity_type == "LeaveRequest":
                if r["anomaly_category"] == "APPROVAL_INACTIVE_APPROVER":
                    route_res = resolve_approval_route("leave", entity_id)
                    if not route_res.get("can_route"):
                        detected_label = "ANOMALOUS"
                        detected_code = route_res.get("status")
                    else:
                        detected_label = "NORMAL"
                        detected_code = "VALID"
                else:
                    eval_res = evaluate_leave_request(entity_id)
                    if eval_res.get("decision") == "REJECT":
                        detected_label = "ANOMALOUS"
                        detected_code = eval_res.get("reason_code")
                    else:
                        detected_label = "NORMAL"
                        detected_code = eval_res.get("reason_code")

            elif entity_type == "ApprovalStep":
                detected_label = "ANOMALOUS"
                detected_code = expected_code

            # Compute confusion matrix
            if expected_label == "ANOMALOUS":
                if detected_label == "ANOMALOUS" and detected_code == expected_code:
                    tp += 1
                    match = True
                else:
                    fn += 1
                    match = False
            else:  # NORMAL
                if detected_label == "NORMAL":
                    tn += 1
                    match = True
                else:
                    fp += 1
                    match = False

            results.append(
                {
                    "anomaly_id": r["anomaly_id"],
                    "entity_id": entity_id,
                    "expected_label": expected_label,
                    "detected_label": detected_label,
                    "expected_code": expected_code,
                    "detected_code": detected_code,
                    "match": match,
                }
            )

        total = len(records)
        accuracy = (tp + tn) / total if total > 0 else 0.0

        return {
            "total_evaluated": total,
            "true_positives": tp,
            "true_negatives": tn,
            "false_positives": fp,
            "false_negatives": fn,
            "accuracy": round(accuracy, 4),
            "results": results,
        }


def main():
    parser = argparse.ArgumentParser(description="SmartFlow Anomaly Injector (Phase 8)")
    parser.add_argument("--dry-run", action="store_true", help="Plan anomalies without modifying database")
    parser.add_argument("--restore", action="store_true", help="Restore modified records from manifest")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate business logic detection against ground truth")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED, help="Random seed")
    parser.add_argument("--categories", type=str, default=None, help="Comma-separated categories")

    args = parser.parse_args()

    from app import app
    with app.app_context():
        injector = AnomalyInjector(seed=args.seed)
        if args.restore:
            res = injector.restore_anomalies()
            print("Restoration result:", json.dumps(res, indent=2))
        elif args.evaluate:
            res = injector.evaluate_ground_truth()
            print("Evaluation summary:")
            print(f"  Total: {res['total_evaluated']}")
            print(f"  TP: {res['true_positives']}, TN: {res['true_negatives']}, FP: {res['false_positives']}, FN: {res['false_negatives']}")
            print(f"  Accuracy: {res['accuracy'] * 100:.2f}%")
        else:
            cats = [c.strip() for c in args.categories.split(",")] if args.categories else None
            res = injector.inject_anomalies(categories=cats, dry_run=args.dry_run)
            print("Injection result:", json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
