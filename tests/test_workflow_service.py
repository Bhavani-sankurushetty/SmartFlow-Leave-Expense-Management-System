"""Unit tests for Phase 9 Workflow Orchestration Service."""

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
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    WorkflowEvent,
    db,
)
from services.approval_service import ApprovalWorkflowError
from services.leave_service import get_available_leave_balance
from services.workflow_service import (
    get_pending_tasks_for_approver,
    get_workflow_state,
    process_step_decision,
    submit_and_route_request,
)


class TestWorkflowService(unittest.TestCase):
    """Test suite covering end-to-end workflow orchestration, authorization, and lifecycle rules."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        cls.app_context.pop()

    def setUp(self):
        self._cleanup_test_records()

        # Find UK employee with direct manager who has sufficient available balance
        self.uk_lpol = LeavePolicy.query.filter_by(country="UK").first()
        self.assertIsNotNone(self.uk_lpol, "UK leave policy must exist.")

        candidates = Employee.query.filter(
            Employee.country == "UK",
            Employee.manager_id.isnot(None),
            Employee.is_active.is_(True),
        ).all()
        self.uk_emp = None
        for c in candidates:
            bal, _ = get_available_leave_balance(c.employee_id, self.uk_lpol.leave_policy_id, 2026)
            if bal >= Decimal("2.00"):
                self.uk_emp = c
                break
        if self.uk_emp is None:
            self.uk_emp = candidates[0]
        self.assertIsNotNone(self.uk_emp, "Must find an active UK employee with manager.")
        self.uk_mgr = Employee.query.filter_by(employee_id=self.uk_emp.manager_id).first()
        self.assertIsNotNone(self.uk_mgr, "Manager must exist.")

        # Find US employee with direct manager whose manager also has a manager (for multi-level testing)
        self.us_emp = Employee.query.filter(
            Employee.country == "USA",
            Employee.manager_id.isnot(None),
            Employee.is_active.is_(True),
        ).first()
        self.assertIsNotNone(self.us_emp, "Must find an active USA employee.")
        self.us_mgr = Employee.query.filter_by(employee_id=self.us_emp.manager_id).first()
        self.us_cc = CostCenter.query.filter_by(cost_center_id=self.us_emp.cost_center_id).first()
        self.us_epol = ExpensePolicy.query.filter_by(country="USA", category="Travel").first()

        # Find an independent third-party employee for unauthorized approver test
        self.unauth_emp = Employee.query.filter(
            Employee.employee_id != self.uk_emp.employee_id,
            Employee.employee_id != self.uk_mgr.employee_id,
            Employee.employee_id != self.us_emp.employee_id,
            Employee.employee_id != self.us_mgr.employee_id,
            Employee.is_active.is_(True),
        ).first()

    def tearDown(self):
        db.session.rollback()
        self._cleanup_test_records()

    def _cleanup_test_records(self):
        try:
            WorkflowEvent.query.filter(
                (WorkflowEvent.entity_id.like("LVE-TWS-%"))
                | (WorkflowEvent.entity_id.like("EXP-TWS-%"))
                | (WorkflowEvent.entity_id.like("APR-LVE-TWS-%"))
                | (WorkflowEvent.entity_id.like("APR-EXP-TWS-%"))
            ).delete(synchronize_session=False)
            ApprovalStep.query.filter(
                (ApprovalStep.leave_request_id.like("LVE-TWS-%"))
                | (ApprovalStep.expense_claim_id.like("EXP-TWS-%"))
            ).delete(synchronize_session=False)
            ExpenseLine.query.filter(ExpenseLine.expense_claim_id.like("EXP-TWS-%")).delete(synchronize_session=False)
            ExpenseClaim.query.filter(ExpenseClaim.expense_claim_id.like("EXP-TWS-%")).delete(synchronize_session=False)
            LeaveRequest.query.filter(LeaveRequest.leave_request_id.like("LVE-TWS-%")).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _create_leave_fixture(
        self,
        req_id: str,
        days: Decimal,
        start: date,
        end: date,
        employee_id: str | None = None,
        policy_id: str | None = None,
    ) -> LeaveRequest:
        l = LeaveRequest(
            leave_request_id=req_id,
            employee_id=employee_id or self.uk_emp.employee_id,
            leave_policy_id=policy_id or self.uk_lpol.leave_policy_id,
            start_date=start,
            end_date=end,
            business_days=days,
            status="submitted",
        )
        db.session.add(l)
        db.session.commit()
        return l

    def _create_expense_fixture(
        self,
        claim_id: str,
        amount: Decimal,
        employee_id: str | None = None,
    ) -> ExpenseClaim:
        emp = Employee.query.filter_by(employee_id=employee_id).first() if employee_id else self.us_emp
        c = ExpenseClaim(
            expense_claim_id=claim_id,
            employee_id=emp.employee_id,
            cost_center_id=emp.cost_center_id,
            expense_policy_id=self.us_epol.expense_policy_id,
            claim_date=date(2026, 6, 1),
            country=emp.country,
            currency="USD",
            total_amount=amount,
            status="submitted",
        )
        line = ExpenseLine(
            expense_line_id=f"EXPL-{claim_id}-01",
            expense_claim_id=claim_id,
            line_number=1,
            category=self.us_epol.category,
            description=f"Business trip for {claim_id} [Receipt: r.pdf | SHA256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef]",
            expense_date=date(2026, 5, 25),
            amount=amount,
            country=emp.country,
        )
        db.session.add(c)
        db.session.add(line)
        db.session.commit()
        return c

    def test_01_eligible_leave_request_routed(self):
        """1. Eligible leave request is evaluated and successfully routed with steps created."""
        req = self._create_leave_fixture("LVE-TWS-01", Decimal("2.00"), date(2026, 11, 2), date(2026, 11, 3))
        res = submit_and_route_request("leave", req.leave_request_id)

        self.assertEqual(res["status"], "ROUTED")
        self.assertTrue(res["can_route"])
        self.assertGreaterEqual(res["steps_count"], 1)

        steps = ApprovalStep.query.filter_by(leave_request_id=req.leave_request_id).all()
        self.assertGreaterEqual(len(steps), 1)
        self.assertEqual(steps[0].approver_employee_id, self.uk_emp.manager_id)
        self.assertEqual(steps[0].decision, "pending")

    def test_02_ineligible_leave_request_rejected_without_steps(self):
        """2. Ineligible leave request (country mismatch or balance exceeded) is rejected and not routed."""
        # Request with US employee against UK policy
        req = self._create_leave_fixture(
            "LVE-TWS-OVER",
            Decimal("3.00"),
            date(2026, 11, 2),
            date(2026, 11, 4),
            employee_id=self.us_emp.employee_id,
            policy_id=self.uk_lpol.leave_policy_id,
        )
        res = submit_and_route_request("leave", req.leave_request_id)

        self.assertEqual(res["status"], "INELIGIBLE")
        self.assertFalse(res["can_route"])
        self.assertFalse(res["is_eligible"])
        self.assertEqual(res["reason_code"], "COUNTRY_POLICY_MISMATCH")
        self.assertEqual(res["steps"], [])

        steps = ApprovalStep.query.filter_by(leave_request_id=req.leave_request_id).all()
        self.assertEqual(len(steps), 0)

        updated_req = LeaveRequest.query.filter_by(leave_request_id=req.leave_request_id).first()
        self.assertEqual(updated_req.status, "rejected")

    def test_03_multi_level_approval_routing(self):
        """3. Requests exceeding level 1 threshold require multi-level approval."""
        # USA expense > 1000 requires Level 2 (2 steps)
        claim = self._create_expense_fixture("EXP-TWS-MULTI", Decimal("1968.00"))
        res = submit_and_route_request("expense", claim.expense_claim_id)

        self.assertEqual(res["status"], "ROUTED")
        self.assertGreaterEqual(res["steps_count"], 2)

        steps = (
            ApprovalStep.query.filter_by(expense_claim_id=claim.expense_claim_id)
            .order_by(ApprovalStep.step_order.asc())
            .all()
        )
        self.assertEqual(steps[0].step_order, 1)
        self.assertEqual(steps[1].step_order, 2)
        self.assertNotEqual(steps[0].approver_employee_id, steps[1].approver_employee_id)

    def test_04_idempotent_route_retry(self):
        """4. Repeated routing of an already-routed request is idempotent."""
        req = self._create_leave_fixture("LVE-TWS-IDEM", Decimal("2.00"), date(2026, 11, 2), date(2026, 11, 3))
        res1 = submit_and_route_request("leave", req.leave_request_id)
        self.assertEqual(res1["status"], "ROUTED")

        res2 = submit_and_route_request("leave", req.leave_request_id)
        self.assertEqual(res2["status"], "ALREADY_ROUTED")
        self.assertEqual(res2["steps_count"], res1["steps_count"])

    def test_05_prevent_self_approval(self):
        """5. Submitter attempting to approve their own request is rejected."""
        req = self._create_leave_fixture("LVE-TWS-SELF", Decimal("2.00"), date(2026, 11, 2), date(2026, 11, 3))
        submit_and_route_request("leave", req.leave_request_id)
        step = ApprovalStep.query.filter_by(leave_request_id=req.leave_request_id).first()

        with self.assertRaises(ApprovalWorkflowError) as cm:
            process_step_decision(step.approval_step_id, self.uk_emp.employee_id, "approved")
        self.assertEqual(cm.exception.code, "SELF_APPROVAL_PROHIBITED")

    def test_06_prevent_unauthorized_approver(self):
        """6. An employee not assigned as approver cannot decide the step."""
        req = self._create_leave_fixture("LVE-TWS-UNAUTH", Decimal("2.00"), date(2026, 11, 2), date(2026, 11, 3))
        submit_and_route_request("leave", req.leave_request_id)
        step = ApprovalStep.query.filter_by(leave_request_id=req.leave_request_id).first()

        with self.assertRaises(ApprovalWorkflowError) as cm:
            process_step_decision(step.approval_step_id, self.unauth_emp.employee_id, "approved")
        self.assertEqual(cm.exception.code, "UNAUTHORIZED_APPROVER")

    def test_07_prevent_out_of_order_approval(self):
        """7. Approver cannot decide step 2 while step 1 remains pending."""
        claim = self._create_expense_fixture("EXP-TWS-ORDER", Decimal("1968.00"))
        submit_and_route_request("expense", claim.expense_claim_id)

        steps = (
            ApprovalStep.query.filter_by(expense_claim_id=claim.expense_claim_id)
            .order_by(ApprovalStep.step_order.asc())
            .all()
        )
        if len(steps) >= 2:
            step1, step2 = steps[0], steps[1]
            with self.assertRaises(ApprovalWorkflowError) as cm:
                process_step_decision(step2.approval_step_id, step2.approver_employee_id, "approved")
            self.assertEqual(cm.exception.code, "PENDING_PRIOR_STEPS")

    def test_08_rejected_step_terminates_workflow(self):
        """8. Rejection of an approval step immediately sets parent request to rejected."""
        req = self._create_leave_fixture("LVE-TWS-REJ", Decimal("2.00"), date(2026, 11, 2), date(2026, 11, 3))
        submit_and_route_request("leave", req.leave_request_id)
        step = ApprovalStep.query.filter_by(leave_request_id=req.leave_request_id).first()

        res = process_step_decision(step.approval_step_id, step.approver_employee_id, "rejected", comments="Denied.")
        self.assertEqual(res["decision"], "rejected")
        self.assertEqual(res["parent_request_status"], "rejected")
        self.assertTrue(res["request_completed"])

        updated_req = LeaveRequest.query.filter_by(leave_request_id=req.leave_request_id).first()
        self.assertEqual(updated_req.status, "rejected")
        self.assertIsNotNone(updated_req.decided_at)

    def test_09_final_approval_completes_workflow(self):
        """9. Approving all steps completes the workflow and sets parent request to approved."""
        claim = self._create_expense_fixture("EXP-TWS-FINAL", Decimal("250.00"))
        submit_and_route_request("expense", claim.expense_claim_id)
        steps = ApprovalStep.query.filter_by(expense_claim_id=claim.expense_claim_id).order_by(ApprovalStep.step_order.asc()).all()

        for step in steps:
            res = process_step_decision(step.approval_step_id, step.approver_employee_id, "approved", comments="All good.")

        self.assertEqual(res["decision"], "approved")
        self.assertEqual(res["parent_request_status"], "approved")
        self.assertTrue(res["request_completed"])

        updated_claim = ExpenseClaim.query.filter_by(expense_claim_id=claim.expense_claim_id).first()
        self.assertEqual(updated_claim.status, "approved")
        self.assertIsNotNone(updated_claim.decided_at)

    def test_10_workflow_events_recorded(self):
        """10. Workflow transitions create an append-only audit trail in WorkflowEvent."""
        claim = self._create_expense_fixture("EXP-TWS-EVTS", Decimal("200.00"))
        submit_and_route_request("expense", claim.expense_claim_id)
        step = ApprovalStep.query.filter_by(expense_claim_id=claim.expense_claim_id).first()
        process_step_decision(step.approval_step_id, step.approver_employee_id, "approved")

        events = WorkflowEvent.query.filter(WorkflowEvent.entity_id.like("%EXP-TWS-EVTS%")).all()
        types = [e.event_type for e in events]

        self.assertIn("REQUEST_SUBMITTED", types)
        self.assertIn("ELIGIBILITY_EVALUATED", types)
        self.assertIn("ROUTE_RESOLVED", types)
        self.assertIn("STEP_CREATED", types)
        self.assertIn("STEP_DECIDED", types)
        self.assertIn("FINAL_DECISION", types)

    def test_11_pending_tasks_for_approver(self):
        """11. Actionable pending tasks are queryable by approver employee ID."""
        req = self._create_leave_fixture("LVE-TWS-TASK", Decimal("2.00"), date(2026, 11, 2), date(2026, 11, 3))
        submit_and_route_request("leave", req.leave_request_id)
        step = ApprovalStep.query.filter_by(leave_request_id=req.leave_request_id).first()

        tasks = get_pending_tasks_for_approver(step.approver_employee_id)
        self.assertTrue(any(t["request_id"] == req.leave_request_id for t in tasks))

    def test_12_get_workflow_state(self):
        """12. get_workflow_state returns consolidated request, step, and audit event state."""
        claim = self._create_expense_fixture("EXP-TWS-STATE", Decimal("250.00"))
        submit_and_route_request("expense", claim.expense_claim_id)

        state = get_workflow_state("expense", claim.expense_claim_id)
        self.assertEqual(state["request_id"], claim.expense_claim_id)
        self.assertEqual(state["status"], "submitted")
        self.assertGreaterEqual(len(state["approval_steps"]), 1)
        self.assertFalse(state["is_approval_complete"])
        self.assertGreaterEqual(len(state["event_timeline"]), 3)


if __name__ == "__main__":
    unittest.main()
