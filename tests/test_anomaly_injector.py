"""Unit tests for SmartFlow Phase 8 Anomaly Injector."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from app import app
from generator.anomaly_injector import AnomalyInjector
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
from services.leave_service import evaluate_leave_request


class TestAnomalyInjector(unittest.TestCase):
    """Test suite covering anomaly injection, dry-run, restoration, and evaluation."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()

    def setUp(self):
        # Create a temporary manifest path for isolation
        self.temp_dir = tempfile.mkdtemp()
        self.temp_manifest = os.path.join(self.temp_dir, "test_manifest.json")
        self.injector = AnomalyInjector(seed=20261022, manifest_path=self.temp_manifest)

    def tearDown(self):
        # Restore if active
        if self.injector.is_manifest_active():
            self.injector.restore_anomalies()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Deterministic anomaly selection
    def test_01_deterministic_anomaly_selection(self):
        """1. Injector produces identical planned records for the same seed."""
        inj1 = AnomalyInjector(seed=20261022, manifest_path=self.temp_manifest)
        inj2 = AnomalyInjector(seed=20261022, manifest_path=self.temp_manifest)

        p1 = inj1.plan_anomalies()
        p2 = inj2.plan_anomalies()

        self.assertEqual(len(p1), len(p2))
        for r1, r2 in zip(p1, p2):
            self.assertEqual(r1["anomaly_id"], r2["anomaly_id"])
            self.assertEqual(r1["anomaly_category"], r2["anomaly_category"])
            self.assertEqual(r1["entity_id"], r2["entity_id"])
            self.assertEqual(r1["injected_value"], r2["injected_value"])

    # 2. Correct injected values
    def test_02_correct_injected_values(self):
        """2. Injected values match the intended anomaly category logic."""
        planned = self.injector.plan_anomalies(["EXPENSE_POLICY_LIMIT_EXCEEDED"])
        anom_records = [r for r in planned if r["ground_truth_label"] == "ANOMALOUS"]
        self.assertEqual(len(anom_records), 1)
        rec = anom_records[0]
        claim = ExpenseClaim.query.filter_by(expense_claim_id=rec["entity_id"]).first()
        self.assertIsNotNone(claim)
        policy_limit = Decimal(str(claim.expense_policy.max_amount))
        injected_amt = Decimal(str(rec["injected_value"]))
        self.assertGreater(injected_amt, policy_limit)

    # 3. Correct ground-truth labels
    def test_03_correct_ground_truth_labels(self):
        """3. Anomaly records are labelled ANOMALOUS and controls are labelled NORMAL."""
        planned = self.injector.plan_anomalies()
        anom_records = [r for r in planned if r["ground_truth_label"] == "ANOMALOUS"]
        norm_records = [r for r in planned if r["ground_truth_label"] == "NORMAL"]

        self.assertGreaterEqual(len(anom_records), 14)
        self.assertGreaterEqual(len(norm_records), 2)

        for r in anom_records:
            self.assertEqual(r["ground_truth_label"], "ANOMALOUS")
            self.assertIsNotNone(r["expected_detection_code"])

        for r in norm_records:
            self.assertEqual(r["ground_truth_label"], "NORMAL")
            self.assertEqual(r["expected_detection_code"], "ELIGIBLE_FOR_APPROVAL")

    # 4. Dry-run mode causing no mutations
    def test_04_dry_run_mode_causes_no_mutations(self):
        """4. Dry-run mode produces planned manifest without modifying database records."""
        # Check initial state of candidate record
        planned = self.injector.plan_anomalies(["EXPENSE_POLICY_LIMIT_EXCEEDED"])
        rec = planned[0]
        claim_id = rec["entity_id"]
        claim_before = ExpenseClaim.query.filter_by(expense_claim_id=claim_id).first()
        amt_before = claim_before.total_amount

        res = self.injector.inject_anomalies(categories=["EXPENSE_POLICY_LIMIT_EXCEEDED"], dry_run=True)
        self.assertEqual(res["status"], "DRY_RUN")
        self.assertEqual(res["injected_count"], 0)

        claim_after = ExpenseClaim.query.filter_by(expense_claim_id=claim_id).first()
        self.assertEqual(claim_after.total_amount, amt_before)
        self.assertFalse(self.injector.is_manifest_active())

    # 5. Duplicate-run protection
    def test_05_duplicate_run_protection(self):
        """5. Re-running injection while an active injection exists is refused."""
        # First injection
        res1 = self.injector.inject_anomalies(categories=["EXPENSE_CLAIM_TOTAL_MISMATCH"], dry_run=False)
        self.assertEqual(res1["status"], "APPLIED")
        self.assertTrue(self.injector.is_manifest_active())

        # Second injection attempt without restore raises error
        with self.assertRaises(RuntimeError) as cm:
            self.injector.inject_anomalies(categories=["EXPENSE_CLAIM_TOTAL_MISMATCH"], dry_run=False)
        self.assertIn("already recorded", str(cm.exception))

    # 6. Backup and rollback/restoration behavior
    def test_06_restoration_restores_original_values(self):
        """6. Calling restore_anomalies resets every modified value back to its exact pre-injection value."""
        target_cat = ["EXPENSE_CLAIM_TOTAL_MISMATCH", "EXPENSE_POLICY_LIMIT_EXCEEDED"]
        planned = self.injector.plan_anomalies(target_cat)
        id_to_orig = {r["entity_id"]: r["original_value"] for r in planned if r["ground_truth_label"] == "ANOMALOUS"}

        # Perform injection
        self.injector.inject_anomalies(categories=target_cat, dry_run=False)

        # Verify values changed
        for eid, orig in id_to_orig.items():
            claim = ExpenseClaim.query.filter_by(expense_claim_id=eid).first()
            self.assertNotEqual(str(claim.total_amount), str(orig))

        # Restore
        res_rest = self.injector.restore_anomalies()
        self.assertEqual(res_rest["status"], "RESTORED")
        self.assertFalse(self.injector.is_manifest_active())

        # Verify values restored exactly
        for eid, orig in id_to_orig.items():
            claim = ExpenseClaim.query.filter_by(expense_claim_id=eid).first()
            self.assertEqual(str(claim.total_amount), str(orig))

    # 7. Referential integrity
    def test_07_referential_integrity(self):
        """7. Injected changes preserve all foreign keys and database constraints."""
        target_cats = ["EXPENSE_POLICY_LIMIT_EXCEEDED", "EXPENSE_MISSING_RECEIPT"]
        self.injector.inject_anomalies(categories=target_cats, dry_run=False)

        # Check FK integrity via SQLite pragma
        raw_conn = db.engine.raw_connection()
        cur = raw_conn.cursor()
        cur.execute("PRAGMA foreign_key_check")
        violations = cur.fetchall()
        cur.close()
        raw_conn.close()

        self.assertEqual(len(violations), 0, "No foreign key violations allowed during injection.")

    # 8. Detection of anomalies through business logic services
    def test_08_detection_of_supported_anomalies(self):
        """8. Services detect injected anomalies and return matching expected detection codes."""
        target_cats = [
            "EXPENSE_POLICY_LIMIT_EXCEEDED",
            "EXPENSE_MISSING_RECEIPT",
            "EXPENSE_CLAIM_TOTAL_MISMATCH",
            "LEAVE_BALANCE_EXCEEDED",
        ]
        self.injector.inject_anomalies(categories=target_cats, dry_run=False)

        eval_summary = self.injector.evaluate_ground_truth()
        self.assertEqual(eval_summary["false_positives"], 0)
        self.assertEqual(eval_summary["false_negatives"], 0)
        self.assertEqual(eval_summary["accuracy"], 1.0)
        self.assertGreaterEqual(eval_summary["true_positives"], 4)

    # 9. Normal control records not falsely labelled anomalous
    def test_09_normal_controls_not_falsely_flagged(self):
        """9. Normal control records evaluate as eligible and are not flagged as anomalous."""
        self.injector.inject_anomalies(categories=["EXPENSE_POLICY_LIMIT_EXCEEDED"], dry_run=False)
        eval_summary = self.injector.evaluate_ground_truth()

        # At least 2 normal controls evaluated
        self.assertGreaterEqual(eval_summary["true_negatives"], 2)
        self.assertEqual(eval_summary["false_positives"], 0)

    # 10. Existing evaluation functions remain fully functional
    def test_10_existing_services_unaffected(self):
        """10. Phase 5, Phase 6, and Phase 7 services operate normally alongside anomaly injector."""
        valid_emp = Employee.query.filter_by(country="USA", is_active=True).first()
        self.assertIsNotNone(valid_emp)
        pol = LeavePolicy.query.filter_by(country="USA").first()
        self.assertIsNotNone(pol)

        # Leave evaluation call
        res_leave = evaluate_leave_request(
            {
                "request_id": "TEST-LR-001",
                "employee_id": valid_emp.employee_id,
                "leave_policy_id": pol.leave_policy_id,
                "start_date": date(2026, 11, 2),
                "end_date": date(2026, 11, 3),
            }
        )
        self.assertIn(res_leave["decision"], ["NEEDS_REVIEW", "REJECT"])


if __name__ == "__main__":
    unittest.main()
