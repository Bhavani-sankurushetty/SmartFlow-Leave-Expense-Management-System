"""Unit tests for SmartFlow Phase 7 Approval Routing Service."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from app import app
from models import (
    ApprovalRule,
    ApprovalStep,
    CostCenter,
    Employee,
    ExpenseClaim,
    ExpenseLine,
    ExpensePolicy,
    LeavePolicy,
    LeaveRequest,
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
from services.leave_service import get_available_leave_balance


class TestApprovalService(unittest.TestCase):
    """Test suite covering the 14 required approval workflow scenarios."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()

    def setUp(self):
        self.created_step_ids: list[str] = []
        self.created_leave_ids: list[str] = []
        self.created_expense_ids: list[str] = []

        # Find valid active employees
        self.ceo = Employee.query.filter_by(job_title="Chief Executive Officer", is_active=True).first()
        self.assertIsNotNone(self.ceo, "CEO must exist.")

        # USA Employee with valid direct manager, multi-level manager hierarchy, and sufficient available leave balance
        candidates = Employee.query.filter(
            Employee.country == "USA",
            Employee.manager_id.isnot(None),
            Employee.is_active.is_(True),
            Employee.employee_id != self.ceo.employee_id,
        ).all()
        self.emp = None
        for candidate in candidates:
            emp_pol = LeavePolicy.query.filter_by(country="USA").first()
            if emp_pol:
                bal, _ = get_available_leave_balance(candidate.employee_id, emp_pol.leave_policy_id, 2026)
                if bal >= Decimal("5.00"):
                    mgr = Employee.query.filter_by(employee_id=candidate.manager_id).first()
                    if mgr and mgr.is_active and mgr.manager_id:
                        self.emp = candidate
                        break
        if self.emp is None:
            self.emp = candidates[0]
        self.assertIsNotNone(self.emp, "Must have an active employee with a manager.")
        self.mgr = Employee.query.filter_by(employee_id=self.emp.manager_id).first()
        self.assertIsNotNone(self.mgr, "Manager must exist.")

        # Find inactive employee
        self.inactive_emp = Employee.query.filter_by(is_active=False).first()

    def tearDown(self):
        # Clean up any test records created during test
        try:
            if self.created_step_ids:
                ApprovalStep.query.filter(ApprovalStep.approval_step_id.in_(self.created_step_ids)).delete(
                    synchronize_session=False
                )
            if self.created_leave_ids:
                ApprovalStep.query.filter(ApprovalStep.leave_request_id.in_(self.created_leave_ids)).delete(
                    synchronize_session=False
                )
                LeaveRequest.query.filter(LeaveRequest.leave_request_id.in_(self.created_leave_ids)).delete(
                    synchronize_session=False
                )
            if self.created_expense_ids:
                ApprovalStep.query.filter(ApprovalStep.expense_claim_id.in_(self.created_expense_ids)).delete(
                    synchronize_session=False
                )
                ExpenseLine.query.filter(ExpenseLine.expense_claim_id.in_(self.created_expense_ids)).delete(
                    synchronize_session=False
                )
                ExpenseClaim.query.filter(ExpenseClaim.expense_claim_id.in_(self.created_expense_ids)).delete(
                    synchronize_session=False
                )
            db.session.commit()
        except Exception:
            db.session.rollback()

    # 1. Valid leave approval route
    def test_01_valid_leave_approval_route(self):
        """1. A valid leave request resolves a valid route with the direct manager."""
        test_req_id = "LVE-TEST-000001"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()
        self.assertIsNotNone(pol)

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        route = resolve_approval_route("leave", test_req_id)
        self.assertTrue(route["can_route"])
        self.assertEqual(route["status"], "VALID")
        self.assertEqual(len(route["steps"]), 1)
        self.assertEqual(route["steps"][0]["step_order"], 1)
        self.assertEqual(route["steps"][0]["authority_level"], 1)
        self.assertEqual(route["steps"][0]["approver_employee_id"], self.mgr.employee_id)

    # 2. Valid expense approval route
    def test_02_valid_expense_approval_route(self):
        """2. A valid expense claim in Band 1 resolves a valid route with the direct manager."""
        test_claim_id = "EXP-TEST-000001"
        self.created_expense_ids.append(test_claim_id)
        pol = ExpensePolicy.query.filter_by(country=self.emp.country, category="Meals").first()
        self.assertIsNotNone(pol)

        claim = ExpenseClaim(
            expense_claim_id=test_claim_id,
            employee_id=self.emp.employee_id,
            cost_center_id=self.emp.cost_center_id,
            expense_policy_id=pol.expense_policy_id,
            claim_date=date(2026, 6, 15),
            country=self.emp.country,
            currency="USD",
            total_amount=Decimal("150.00"),
            status="submitted",
        )
        db.session.add(claim)
        line = ExpenseLine(
            expense_line_id="EXPL-EXP-TEST-000001-01",
            expense_claim_id=test_claim_id,
            line_number=1,
            category="Meals",
            description="Client lunch [Receipt: lunch.pdf | SHA256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef]",
            expense_date=date(2026, 6, 14),
            amount=Decimal("150.00"),
            country=self.emp.country,
        )
        db.session.add(line)
        db.session.commit()

        route = resolve_approval_route("expense", test_claim_id)
        self.assertTrue(route["can_route"])
        self.assertEqual(route["status"], "VALID")
        self.assertEqual(len(route["steps"]), 1)
        self.assertEqual(route["steps"][0]["step_order"], 1)
        self.assertEqual(route["steps"][0]["authority_level"], 1)
        self.assertEqual(route["steps"][0]["approver_employee_id"], self.mgr.employee_id)

    # 3. Multiple approval levels
    def test_03_multiple_approval_levels(self):
        """3. An expense claim in Band 2 ($500.01-$2000.00) resolves multiple sequential approval levels."""
        test_claim_id = "EXP-TEST-000002"
        self.created_expense_ids.append(test_claim_id)
        pol = ExpensePolicy.query.filter_by(country=self.emp.country, category="Travel").first()

        claim = ExpenseClaim(
            expense_claim_id=test_claim_id,
            employee_id=self.emp.employee_id,
            cost_center_id=self.emp.cost_center_id,
            expense_policy_id=pol.expense_policy_id,
            claim_date=date(2026, 6, 15),
            country=self.emp.country,
            currency="USD",
            total_amount=Decimal("1200.00"),
            status="submitted",
        )
        db.session.add(claim)
        line = ExpenseLine(
            expense_line_id="EXPL-EXP-TEST-000002-01",
            expense_claim_id=test_claim_id,
            line_number=1,
            category="Travel",
            description="Conference travel [Receipt: conf.pdf | SHA256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef]",
            expense_date=date(2026, 6, 14),
            amount=Decimal("1200.00"),
            country=self.emp.country,
        )
        db.session.add(line)
        db.session.commit()

        route = resolve_approval_route("expense", test_claim_id)
        self.assertTrue(route["can_route"])
        self.assertEqual(route["status"], "VALID")
        # Band 2 requires 2 steps: Step 1 (Manager), Step 2 (Department Head)
        self.assertEqual(len(route["steps"]), 2)
        self.assertEqual(route["steps"][0]["step_order"], 1)
        self.assertEqual(route["steps"][0]["authority_level"], 1)
        self.assertEqual(route["steps"][1]["step_order"], 2)
        self.assertEqual(route["steps"][1]["authority_level"], 2)
        # Approvers must be distinct
        self.assertNotEqual(route["steps"][0]["approver_employee_id"], route["steps"][1]["approver_employee_id"])

    # 4. No matching approval rule
    def test_04_no_matching_approval_rule(self):
        """4. A request with unsupported country or criteria returns NO_MATCHING_RULE."""
        test_req_id = "LVE-TEST-000004"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("-5.00"),  # Negative business days has no rule
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        route = resolve_approval_route("leave", test_req_id, skip_eligibility_check=True)
        self.assertFalse(route["can_route"])
        self.assertEqual(route["status"], "NO_MATCHING_RULE")

    # 5. Missing or inactive approver
    def test_05_missing_or_inactive_approver(self):
        """5. A request where the required approver is missing or inactive returns INACTIVE_OR_MISSING_APPROVER."""
        # CEO has no manager
        test_req_id = "LVE-TEST-000005"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.ceo.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.ceo.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        route = resolve_approval_route("leave", test_req_id, skip_eligibility_check=True)
        self.assertFalse(route["can_route"])
        self.assertEqual(route["status"], "INACTIVE_OR_MISSING_APPROVER")

    # 6. Unauthorized approver
    def test_06_unauthorized_approver(self):
        """6. An employee who is not the designated approver cannot decide the step."""
        test_req_id = "LVE-TEST-000006"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        steps = create_approval_steps("leave", test_req_id)
        self.assertEqual(len(steps), 1)
        step = steps[0]

        # Wrong approver attempts approval
        wrong_approver = self.emp.employee_id  # Submitter cannot approve
        with self.assertRaises(ApprovalWorkflowError) as cm:
            record_approval(step.approval_step_id, wrong_approver)
        self.assertEqual(cm.exception.code, "UNAUTHORIZED_APPROVER")

    # 7. Approval of a pending step
    def test_07_approval_of_pending_step(self):
        """7. Authorized approver successfully approves a pending step, completing single-step request."""
        test_req_id = "LVE-TEST-000007"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        steps = create_approval_steps("leave", test_req_id)
        step = steps[0]

        res = record_approval(step.approval_step_id, step.approver_employee_id, comments="Approved by direct manager")
        self.assertEqual(res["decision"], "approved")
        self.assertTrue(res["request_completed"])
        self.assertEqual(res["parent_request_status"], "approved")

        # Verify DB persistence
        updated_req = LeaveRequest.query.filter_by(leave_request_id=test_req_id).first()
        self.assertEqual(updated_req.status, "approved")
        self.assertIsNotNone(updated_req.decided_at)
        self.assertTrue(is_approval_complete("leave", test_req_id))

    # 8. Rejection of a pending step
    def test_08_rejection_of_pending_step(self):
        """8. Authorized approver rejects a pending step, immediately transitioning request to rejected."""
        test_req_id = "LVE-TEST-000008"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        steps = create_approval_steps("leave", test_req_id)
        step = steps[0]

        res = record_rejection(step.approval_step_id, step.approver_employee_id, comments="Coverage unavailable")
        self.assertEqual(res["decision"], "rejected")
        self.assertTrue(res["request_completed"])
        self.assertEqual(res["parent_request_status"], "rejected")

        updated_req = LeaveRequest.query.filter_by(leave_request_id=test_req_id).first()
        self.assertEqual(updated_req.status, "rejected")
        self.assertIsNotNone(updated_req.decided_at)

    # 9. Attempted approval while other required steps remain pending
    def test_09_attempted_approval_while_prior_steps_pending(self):
        """9. Approver for step 2 cannot approve while step 1 is still pending."""
        test_claim_id = "EXP-TEST-000009"
        self.created_expense_ids.append(test_claim_id)
        pol = ExpensePolicy.query.filter_by(country=self.emp.country, category="Travel").first()

        claim = ExpenseClaim(
            expense_claim_id=test_claim_id,
            employee_id=self.emp.employee_id,
            cost_center_id=self.emp.cost_center_id,
            expense_policy_id=pol.expense_policy_id,
            claim_date=date(2026, 6, 15),
            country=self.emp.country,
            currency="USD",
            total_amount=Decimal("1200.00"),
            status="submitted",
        )
        db.session.add(claim)
        line = ExpenseLine(
            expense_line_id="EXPL-EXP-TEST-000009-01",
            expense_claim_id=test_claim_id,
            line_number=1,
            category="Travel",
            description="Conference travel [Receipt: conf.pdf | SHA256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef]",
            expense_date=date(2026, 6, 14),
            amount=Decimal("1200.00"),
            country=self.emp.country,
        )
        db.session.add(line)
        db.session.commit()

        steps = create_approval_steps("expense", test_claim_id)
        self.assertEqual(len(steps), 2)
        step1 = steps[0]
        step2 = steps[1]

        # Step 2 approver attempts to approve before step 1 is decided
        with self.assertRaises(ApprovalWorkflowError) as cm:
            record_approval(step2.approval_step_id, step2.approver_employee_id)
        self.assertEqual(cm.exception.code, "PENDING_PRIOR_STEPS")

    # 10. Duplicate approval attempts
    def test_10_duplicate_approval_attempts(self):
        """10. Attempting to approve an already decided step is rejected."""
        test_req_id = "LVE-TEST-000010"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        steps = create_approval_steps("leave", test_req_id)
        step = steps[0]

        record_approval(step.approval_step_id, step.approver_employee_id)

        # Second approval attempt on the same step
        with self.assertRaises(ApprovalWorkflowError) as cm:
            record_approval(step.approval_step_id, step.approver_employee_id)
        self.assertEqual(cm.exception.code, "STEP_ALREADY_DECIDED")

    # 11. Invalid state transitions
    def test_11_invalid_state_transitions(self):
        """11. Invalid decisions or invalid state transitions raise clear workflow errors."""
        test_req_id = "LVE-TEST-000011"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        steps = create_approval_steps("leave", test_req_id)
        step = steps[0]

        # Invalid decision string
        with self.assertRaises(ApprovalWorkflowError) as cm:
            record_decision(step.approval_step_id, step.approver_employee_id, "tentative")
        self.assertEqual(cm.exception.code, "INVALID_DECISION")

    # 12. Database transaction rollback after failure
    def test_12_transaction_rollback_after_failure(self):
        """12. A failure during decision recording leaves database state completely rolled back."""
        test_req_id = "LVE-TEST-000012"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        steps = create_approval_steps("leave", test_req_id)
        step = steps[0]

        # Unauthorized attempt raises error
        try:
            record_approval(step.approval_step_id, "UNAUTHORIZED_ID")
        except ApprovalWorkflowError:
            pass

        # Step should remain untouched as 'pending'
        refreshed_step = ApprovalStep.query.filter_by(approval_step_id=step.approval_step_id).first()
        self.assertEqual(refreshed_step.decision, "pending")
        self.assertIsNone(refreshed_step.decided_at)

    # 13. Correct behavior for already approved or rejected requests
    def test_13_correct_behavior_for_already_approved_or_rejected(self):
        """13. Attempting to create approval steps for already finalized requests is rejected."""
        test_req_id = "LVE-TEST-000013"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="approved",  # Already approved
        )
        db.session.add(req)
        db.session.commit()

        with self.assertRaises(ApprovalWorkflowError) as cm:
            create_approval_steps("leave", test_req_id)
        self.assertEqual(cm.exception.code, "INVALID_STATE_TRANSITION")

    # 14. Integration with eligibility services
    def test_14_integration_with_eligibility_services(self):
        """14. A request that fails business eligibility evaluation cannot be routed for approval."""
        test_claim_id = "EXP-TEST-000014"
        self.created_expense_ids.append(test_claim_id)
        pol = ExpensePolicy.query.filter_by(country=self.emp.country, category="Travel").first()

        # Create claim with negative amount -> fails eligibility
        claim = ExpenseClaim(
            expense_claim_id=test_claim_id,
            employee_id=self.emp.employee_id,
            cost_center_id=self.emp.cost_center_id,
            expense_policy_id=pol.expense_policy_id,
            claim_date=date(2026, 6, 15),
            country=self.emp.country,
            currency="USD",
            total_amount=Decimal("-50.00"),
            status="submitted",
        )
        db.session.add(claim)
        line = ExpenseLine(
            expense_line_id="EXPL-EXP-TEST-000014-01",
            expense_claim_id=test_claim_id,
            line_number=1,
            category="Travel",
            description="Invalid negative amount",
            expense_date=date(2026, 6, 14),
            amount=Decimal("-50.00"),
            country=self.emp.country,
        )
        db.session.add(line)
        db.session.commit()

        route = resolve_approval_route("expense", test_claim_id)
        self.assertFalse(route["can_route"])
        self.assertEqual(route["status"], "INELIGIBLE")

        with self.assertRaises(ApprovalWorkflowError) as cm:
            create_approval_steps("expense", test_claim_id)
        self.assertEqual(cm.exception.code, "INELIGIBLE")

    def test_15_get_pending_approvals_and_history(self):
        """15. Verifies get_pending_approvals filters correctly and get_request_approval_history returns full audit trail."""
        test_req_id = "LVE-TEST-000015"
        self.created_leave_ids.append(test_req_id)
        pol = LeavePolicy.query.filter_by(country=self.emp.country).first()

        req = LeaveRequest(
            leave_request_id=test_req_id,
            employee_id=self.emp.employee_id,
            leave_policy_id=pol.leave_policy_id,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
            business_days=Decimal("2.00"),
            status="submitted",
        )
        db.session.add(req)
        db.session.commit()

        steps = create_approval_steps("leave", test_req_id)
        step = steps[0]

        pending = get_pending_approvals(approver_employee_id=self.mgr.employee_id, request_type="leave")
        matching = [p for p in pending if p["approval_step_id"] == step.approval_step_id]
        self.assertEqual(len(matching), 1)

        history_before = get_request_approval_history("leave", test_req_id)
        self.assertEqual(len(history_before), 1)
        self.assertEqual(history_before[0]["decision"], "pending")

        record_approval(step.approval_step_id, self.mgr.employee_id, comments="Looks good!")

        history_after = get_request_approval_history("leave", test_req_id)
        self.assertEqual(len(history_after), 1)
        self.assertEqual(history_after[0]["decision"], "approved")
        self.assertEqual(history_after[0]["comments"], "Looks good!")


if __name__ == "__main__":
    unittest.main()
