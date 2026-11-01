"""Unit tests for Phase 9 Generator Workflow Simulation and Reconciliation."""

from __future__ import annotations

import inspect
import unittest
from decimal import Decimal

from app import app
from models import (
    ApprovalStep,
    CostCenter,
    ExpenseClaim,
    LeaveRequest,
    Reimbursement,
    WorkflowEvent,
    db,
)
from services import (
    approval_service,
    expense_service,
    leave_service,
    reimbursement_service,
    workflow_service,
)
from services.expense_service import get_cost_center_budget_utilization


class TestGeneratorWorkflowIntegration(unittest.TestCase):
    """Test suite verifying generator workflow simulation, reimbursement gating, and operational independence."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        cls.app_context.pop()

    def test_01_generated_approval_steps_exist(self):
        """Verify that simulated workflow records populate approval steps with valid parent relationships."""
        step_count = ApprovalStep.query.count()
        if step_count == 0:
            self.skipTest("Database does not contain simulated workflow steps (baseline snapshot mode).")

        self.assertGreater(step_count, 0)
        approved_steps = ApprovalStep.query.filter_by(decision="approved").count()
        rejected_steps = ApprovalStep.query.filter_by(decision="rejected").count()
        pending_steps = ApprovalStep.query.filter_by(decision="pending").count()

        self.assertEqual(step_count, approved_steps + rejected_steps + pending_steps)
        self.assertGreater(approved_steps, 0)

        # Check single-parent constraint on every step
        for s in ApprovalStep.query.limit(200).all():
            has_leave = s.leave_request_id is not None
            has_claim = s.expense_claim_id is not None
            self.assertTrue(has_leave ^ has_claim, f"Step '{s.approval_step_id}' violates single-parent constraint.")

    def test_02_only_fully_approved_claims_receive_reimbursements(self):
        """Verify that reimbursements are created strictly for finalized, fully approved expense claims."""
        reimbursements = Reimbursement.query.all()
        if not reimbursements:
            self.skipTest("Database contains no reimbursements.")

        for reimb in reimbursements:
            claim = reimb.expense_claim
            self.assertIsNotNone(claim, f"Reimbursement '{reimb.reimbursement_id}' has missing claim.")
            self.assertEqual(
                claim.status,
                "approved",
                f"Reimbursement '{reimb.reimbursement_id}' is linked to non-approved claim '{claim.expense_claim_id}' (status: {claim.status}).",
            )
            self.assertEqual(
                reimb.amount,
                claim.total_amount,
                f"Reimbursement amount ({reimb.amount}) does not match claim total ({claim.total_amount}).",
            )
            # Verify every approval step for this claim is approved
            for step in claim.approval_steps:
                self.assertEqual(
                    step.decision,
                    "approved",
                    f"Reimbursed claim '{claim.expense_claim_id}' has non-approved step '{step.approval_step_id}' ({step.decision}).",
                )

    def test_03_rejected_and_pending_claims_not_reimbursed(self):
        """Verify that rejected, submitted, or draft expense claims are never reimbursed."""
        non_approved_claims = ExpenseClaim.query.filter(ExpenseClaim.status != "approved").all()
        for claim in non_approved_claims:
            self.assertEqual(
                len(claim.reimbursements),
                0,
                f"Non-approved claim '{claim.expense_claim_id}' (status: {claim.status}) has a reimbursement!",
            )

    def test_04_budget_calculations_reconcile_after_simulation(self):
        """Verify that cost center annual budget utilization reconciles with approved claims."""
        cost_centers = CostCenter.query.filter_by(is_active=True).all()
        for cc in cost_centers:
            util = get_cost_center_budget_utilization(cc.cost_center_id, 2026)
            budget_used = util["budget_used"]
            budget_remaining = util["budget_remaining"]
            annual_budget = util["annual_budget"]

            self.assertEqual(annual_budget, cc.annual_budget)
            self.assertEqual(budget_remaining, max(Decimal("0.00"), annual_budget - budget_used))
            self.assertGreaterEqual(util["utilization_percentage"], Decimal("0.00"))

    def test_05_workflow_history_persisted(self):
        """Verify that WorkflowEvent audit records are persisted for lifecycle transitions."""
        events = WorkflowEvent.query.all()
        if not events:
            self.skipTest("Database contains no workflow events.")

        event_types = {e.event_type for e in events}
        self.assertIn("REQUEST_SUBMITTED", event_types)
        self.assertIn("ELIGIBILITY_EVALUATED", event_types)
        self.assertIn("FINAL_DECISION", event_types)

        for event in events[:100]:
            self.assertIn(event.request_type, {"leave", "expense"})
            self.assertIsNotNone(event.entity_id)
            self.assertIsNotNone(event.created_at)

    def test_06_ground_truth_labels_not_used_in_operational_code(self):
        """Verify that operational services do not reference ground truth manifest or anomaly labels."""
        services = [
            approval_service,
            expense_service,
            leave_service,
            reimbursement_service,
            workflow_service,
        ]
        forbidden_terms = [
            "anomaly_manifest.json",
            "ground_truth_label",
            "ANOMALOUS",
            "NORMAL_CONTROL",
        ]
        for svc in services:
            source = inspect.getsource(svc)
            for term in forbidden_terms:
                self.assertNotIn(
                    term,
                    source,
                    f"Operational service '{svc.__name__}' references ground-truth label '{term}'.",
                )

    def test_07_country_matched_leave_policy_selection(self):
        """Verify that all normal leave requests use policies matching the employee's country."""
        leaves = LeaveRequest.query.all()
        if not leaves:
            self.skipTest("Database contains no leave requests.")

        mismatches = [
            req.leave_request_id
            for req in leaves
            if req.leave_policy.country != req.employee.country
        ]
        self.assertEqual(
            len(mismatches),
            0,
            f"Found {len(mismatches)} leave requests with mismatched policy country: {mismatches[:5]}",
        )

    def test_08_country_mismatch_exception_handling(self):
        """Verify that deliberate country mismatches are rejected with COUNTRY_POLICY_MISMATCH."""
        from models import Employee, LeavePolicy
        from services.leave_service import evaluate_leave_request
        from datetime import date

        uk_emp = Employee.query.filter_by(country="UK", is_active=True).first()
        ind_pol = LeavePolicy.query.filter_by(country="India", is_active=True).first()
        self.assertIsNotNone(uk_emp)
        self.assertIsNotNone(ind_pol)

        dummy = LeaveRequest(
            leave_request_id="LVE-TEST-MISMATCH-001",
            employee_id=uk_emp.employee_id,
            leave_policy_id=ind_pol.leave_policy_id,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 2),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        res = evaluate_leave_request(dummy)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "COUNTRY_POLICY_MISMATCH")

    def test_09_approval_hierarchy_exceptions_preserved(self):
        """Verify that requests with inactive managers or CEO fail routing with INACTIVE_OR_MISSING_APPROVER."""
        from services.approval_service import resolve_approval_route
        from models import Employee

        e_inactive_mgr = Employee.query.filter_by(manager_id="EMP-000047").first()
        if e_inactive_mgr:
            lr = LeaveRequest.query.filter_by(employee_id=e_inactive_mgr.employee_id).first()
            if lr:
                route_res = resolve_approval_route("leave", lr.leave_request_id)
                self.assertFalse(route_res["can_route"])
                self.assertEqual(route_res["status"], "INACTIVE_OR_MISSING_APPROVER")

        ceo = Employee.query.filter_by(manager_id=None).first()
        if ceo:
            ceo_lr = LeaveRequest.query.filter_by(employee_id=ceo.employee_id).first()
            if ceo_lr:
                route_res = resolve_approval_route("leave", ceo_lr.leave_request_id)
                self.assertFalse(route_res["can_route"])
                self.assertEqual(route_res["status"], "INACTIVE_OR_MISSING_APPROVER")

    def test_10_all_seven_required_exception_scenarios(self):
        """Verify that all seven core exception scenarios are covered and detected."""
        from services.leave_service import evaluate_leave_request
        from services.expense_service import evaluate_expense_claim
        from services.reimbursement_service import create_reimbursement_for_approved_claim, ReimbursementError
        from services.workflow_service import process_step_decision
        from models import Employee, ExpensePolicy, CostCenter, ExpenseLine
        from datetime import date

        # 1. Negative leave balance / balance exceeded: covered in test_leave_service
        # 2. Overlapping approved leave: covered in test_leave_service
        # 3. Expense date outside employment tenure: covered in test_expense_service
        # 4. Split claims / policy limit evasion: covered in test_expense_service
        # 5. Missing cost center:
        emp = Employee.query.filter_by(is_active=True).first()
        pol = ExpensePolicy.query.filter_by(country=emp.country, is_active=True).first()
        missing_cc_claim = ExpenseClaim(
            expense_claim_id="EXP-TEST-NO-CC-001",
            employee_id=emp.employee_id,
            cost_center_id="CC-NONEXISTENT",
            expense_policy_id=pol.expense_policy_id,
            claim_date=date(2026, 6, 1),
            country=emp.country,
            currency="USD",
            total_amount=Decimal("100.00"),
            status="submitted",
        )
        res_cc = evaluate_expense_claim(missing_cc_claim)
        self.assertEqual(res_cc["decision"], "REJECT")
        self.assertEqual(res_cc["reason_code"], "COST_CENTER_NOT_FOUND")

        # 6. Approval by requester (self-approval prohibited):
        # Already verified in test_workflow_service.test_05_prevent_self_approval
        # 7. Duplicate reimbursement prohibited:
        # Already verified in test_reimbursement_service.test_06_prevent_duplicate_reimbursement
        self.assertTrue(True)
