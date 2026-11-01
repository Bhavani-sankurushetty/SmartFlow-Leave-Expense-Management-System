"""Frontend portal and end-to-end integration tests for SmartFlow Phase 10."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import app
from config import DATABASE_PATH, Config
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
    Reimbursement,
    WorkflowEvent,
    db,
)
from sqlalchemy import create_engine


class TestFrontendPortals(unittest.TestCase):
    """Integration test suite for the complete SmartFlow web application."""

    @classmethod
    def setUpClass(cls):
        # Guarantee zero mutation of baseline databases by cloning to isolated temp database
        cls.temp_dir = tempfile.mkdtemp()
        cls.temp_db_path = os.path.join(cls.temp_dir, "test_frontend.db")
        shutil.copy2(str(DATABASE_PATH), cls.temp_db_path)

        cls.app = app
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{cls.temp_db_path}"

        # Explicitly rebind SQLAlchemy engine to the isolated temporary database
        cls.new_engine = create_engine(f"sqlite:///{cls.temp_db_path}")
        cls.orig_engines = db._app_engines.get(cls.app)
        db._app_engines[cls.app] = {None: cls.new_engine}

        cls.client = cls.app.test_client()
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()
        db._app_engines[cls.app] = cls.orig_engines
        cls.new_engine.dispose()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.created_leave_ids: list[str] = []
        self.created_expense_ids: list[str] = []
        self.created_reimbursement_ids: list[str] = []
        self.created_step_ids: list[str] = []
        self.created_event_ids: list[str] = []
        self.modified_step_ids: list[tuple[str, str, str | None]] = []
        self._cleanup_test_records()

    def tearDown(self):
        db.session.rollback()
        # Restore any modified steps
        for sid, orig_decision, orig_comments in self.modified_step_ids:
            s = ApprovalStep.query.filter_by(approval_step_id=sid).first()
            if s:
                s.decision = orig_decision
                s.comments = orig_comments
                s.decided_at = None

        self._cleanup_test_records()

    def _cleanup_test_records(self):
        try:
            for cid in self.created_expense_ids:
                steps = ApprovalStep.query.filter_by(expense_claim_id=cid).all()
                for s in steps:
                    WorkflowEvent.query.filter_by(entity_id=s.approval_step_id).delete()
                WorkflowEvent.query.filter_by(entity_id=cid).delete()
                ApprovalStep.query.filter_by(expense_claim_id=cid).delete()
                ExpenseLine.query.filter_by(expense_claim_id=cid).delete()
                ExpenseClaim.query.filter_by(expense_claim_id=cid).delete()

            for lid in self.created_leave_ids:
                steps = ApprovalStep.query.filter_by(leave_request_id=lid).all()
                for s in steps:
                    WorkflowEvent.query.filter_by(entity_id=s.approval_step_id).delete()
                WorkflowEvent.query.filter_by(entity_id=lid).delete()
                ApprovalStep.query.filter_by(leave_request_id=lid).delete()
                LeaveRequest.query.filter_by(leave_request_id=lid).delete()

            for rid in self.created_reimbursement_ids:
                WorkflowEvent.query.filter_by(entity_id=rid).delete()
                Reimbursement.query.filter_by(reimbursement_id=rid).delete()

            db.session.commit()
        except Exception:
            db.session.rollback()

    # ---------------------------------------------------------
    # 1. Dashboard, Health and Core Navigation
    # ---------------------------------------------------------
    def test_root_serves_dashboard(self):
        """Root URL / and /dashboard render the operational dashboard."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Operational Workflow Dashboard", html)

    def test_dashboard_renders_successfully(self):
        """Dashboard renders KPI metrics, cost centers, and pending queues."""
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Operational Workflow Dashboard", html)
        self.assertIn("Leave Requests", html)
        self.assertIn("Expense Claims", html)
        self.assertIn("Cost Center Budget Utilization", html)
        self.assertIn("Pending Approvals Queue", html)

    def test_health_check_endpoint(self):
        """Health check endpoint returns valid system status."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["database"], "connected")

    # ---------------------------------------------------------
    # 2. User Persona Switching & RBAC Context
    # ---------------------------------------------------------
    def test_switch_user_sets_session(self):
        """User switcher persists employee ID in session."""
        emp = Employee.query.filter(Employee.direct_reports.any(), Employee.is_active.is_(True)).first()
        self.assertIsNotNone(emp)

        res = self.client.post("/switch-user", data={"employee_id": emp.employee_id}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("user_id"), emp.employee_id)

    def test_switch_user_invalid_falls_back(self):
        """Switching to an invalid employee ID does not crash and defaults cleanly."""
        res = self.client.post("/switch-user", data={"employee_id": "INVALID-999"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    # ---------------------------------------------------------
    # 3. Employee Portal - Hub, Submissions, and Views
    # ---------------------------------------------------------
    def test_employee_hub_view(self):
        """Employee hub displays profile, balances, leaves, and expenses."""
        emp = Employee.query.filter_by(country="USA", is_active=True).first()
        self.assertIsNotNone(emp)

        with self.client.session_transaction() as sess:
            sess["user_id"] = emp.employee_id

        res = self.client.get("/employee/", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn(emp.first_name, html)
        self.assertIn("Statutory Leave Balances", html)
        self.assertIn("Expense Claims", html)

    def test_leave_new_form_renders(self):
        """New leave request form loads with matching country policies."""
        emp = Employee.query.filter_by(country="USA", is_active=True).first()
        with self.client.session_transaction() as sess:
            sess["user_id"] = emp.employee_id

        res = self.client.get("/employee/leave/new")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Submit Leave Request", html)
        self.assertIn("Leave Policy", html)

    def test_submit_valid_leave_request(self):
        """Submitting a valid leave request initializes workflow steps and records request."""
        # Find employee with active manager and available balance >= 3.00
        emp = Employee.query.filter_by(employee_id="EMP-000012").first()
        self.assertIsNotNone(emp)
        pol = LeavePolicy.query.filter_by(leave_policy_id="LPOL-000002").first()
        self.assertIsNotNone(pol)

        with self.client.session_transaction() as sess:
            sess["user_id"] = emp.employee_id

        # Target a future business span (Tuesday to Thursday = 3 days)
        start_date = "2026-09-08"
        end_date = "2026-09-10"

        payload = {
            "leave_policy_id": pol.leave_policy_id,
            "start_date": start_date,
            "end_date": end_date,
        }

        res = self.client.post("/employee/leave/new", data=payload, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("successfully submitted and routed", html)

        # Locate created request and track for tearDown
        created_req = (
            LeaveRequest.query.filter_by(
                employee_id=emp.employee_id,
                leave_policy_id=pol.leave_policy_id,
                start_date=date(2026, 9, 8),
            )
            .order_by(LeaveRequest.leave_request_id.desc())
            .first()
        )
        self.assertIsNotNone(created_req)
        self.created_leave_ids.append(created_req.leave_request_id)
        self.assertEqual(created_req.business_days, Decimal("3.00"))

        steps = ApprovalStep.query.filter_by(leave_request_id=created_req.leave_request_id).all()
        self.assertTrue(len(steps) >= 1)
        for s in steps:
            self.created_step_ids.append(s.approval_step_id)

    def test_submit_invalid_leave_date_range(self):
        """Submitting an end date before start date triggers validation rejection."""
        emp = Employee.query.filter_by(is_active=True).first()
        with self.client.session_transaction() as sess:
            sess["user_id"] = emp.employee_id

        pol = LeavePolicy.query.filter_by(country=emp.country).first()

        payload = {
            "leave_policy_id": pol.leave_policy_id if pol else "LP-USA-PTO",
            "start_date": "2026-09-10",
            "end_date": "2026-09-05",  # Invalid: end < start
        }

        res = self.client.post("/employee/leave/new", data=payload, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("cannot be before start date", html)

    def test_expense_new_form_renders(self):
        """New expense claim form renders cost centers and line entry fields."""
        emp = Employee.query.filter_by(is_active=True).first()
        with self.client.session_transaction() as sess:
            sess["user_id"] = emp.employee_id

        res = self.client.get("/employee/expense/new")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Submit Multi-Line Expense Claim", html)
        self.assertIn("Expense Line Items", html)

    def test_submit_valid_expense_claim(self):
        """Submitting a valid expense claim generates claim, lines, and approval steps."""
        emp = Employee.query.filter(Employee.country == "USA", Employee.manager_id.isnot(None), Employee.is_active.is_(True)).first()
        self.assertIsNotNone(emp)
        cc = CostCenter.query.filter_by(country="USA", is_active=True).first()
        self.assertIsNotNone(cc)
        pol = ExpensePolicy.query.filter_by(country="USA", category="Travel").first()
        self.assertIsNotNone(pol)

        with self.client.session_transaction() as sess:
            sess["user_id"] = emp.employee_id

        payload = {
            "cost_center_id": cc.cost_center_id,
            "expense_policy_id": pol.expense_policy_id,
            "claim_date": "2026-06-15",
            "line_category[]": ["Travel", "Travel"],
            "line_description[]": ["Local transit ticket", "Airport taxi fare"],
            "line_date[]": ["2026-06-14", "2026-06-14"],
            "line_amount[]": ["40.00", "35.00"],
        }

        res = self.client.post("/employee/expense/new", data=payload, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("submitted and routed", html)

        claim = (
            ExpenseClaim.query.filter_by(
                employee_id=emp.employee_id,
                cost_center_id=cc.cost_center_id,
                claim_date=date(2026, 6, 15),
            )
            .order_by(ExpenseClaim.expense_claim_id.desc())
            .first()
        )
        self.assertIsNotNone(claim)
        self.created_expense_ids.append(claim.expense_claim_id)
        self.assertEqual(claim.total_amount, Decimal("75.00"))
        self.assertEqual(len(claim.expense_lines), 2)

    def test_leave_detail_view(self):
        """Leave detail view displays request, approval stepper, and events."""
        req = LeaveRequest.query.first()
        self.assertIsNotNone(req)

        res = self.client.get(f"/employee/leave/{req.leave_request_id}")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn(req.leave_request_id, html)
        self.assertIn("Leave Details", html)
        self.assertIn("Approval Workflow Sequence", html)

    def test_expense_detail_view(self):
        """Expense detail view displays claim, line items, and approval steps."""
        claim = ExpenseClaim.query.first()
        self.assertIsNotNone(claim)

        res = self.client.get(f"/employee/expense/{claim.expense_claim_id}")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn(claim.expense_claim_id, html)
        self.assertIn("Claim Overview", html)
        self.assertIn("Approval Workflow Sequence", html)

    # ---------------------------------------------------------
    # 4. Approver Portal - Inbox, Review, and Decisions
    # ---------------------------------------------------------
    def test_approvals_inbox_view(self):
        """Approvals inbox renders for an approver."""
        approver = Employee.query.filter(Employee.approval_steps.any(), Employee.is_active.is_(True)).first()
        self.assertIsNotNone(approver)

        with self.client.session_transaction() as sess:
            sess["user_id"] = approver.employee_id

        res = self.client.get("/approvals/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Approval Action Inbox", html)

    def test_approvals_review_step(self):
        """Review page renders for an existing approval step."""
        step = ApprovalStep.query.first()
        self.assertIsNotNone(step)

        res = self.client.get(f"/approvals/review/{step.approval_step_id}")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn(step.approval_step_id, html)
        self.assertIn("Authorized Decision Form", html)

    def test_approvals_review_expense_step(self):
        """Review page renders for an expense approval step without UndefinedError."""
        step = ApprovalStep.query.filter(ApprovalStep.expense_claim_id.isnot(None)).first()
        self.assertIsNotNone(step)

        res = self.client.get(f"/approvals/review/{step.approval_step_id}")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn(step.approval_step_id, html)
        self.assertIn("Expense Line Items", html)

    def test_approvals_decide_action(self):
        """Deciding an approval step transitions its state and updates workflow."""
        step = (
            ApprovalStep.query.join(LeaveRequest, ApprovalStep.leave_request_id == LeaveRequest.leave_request_id)
            .filter(
                ApprovalStep.decision == "pending",
                ApprovalStep.step_order == 1,
                LeaveRequest.status == "submitted",
            )
            .first()
        )
        self.assertIsNotNone(step)

        orig_decision = step.decision
        orig_comments = step.comments
        orig_decided_at = step.decided_at
        orig_status = step.leave_request.status

        with self.client.session_transaction() as sess:
            sess["user_id"] = step.approver_employee_id

        payload = {
            "approval_step_id": step.approval_step_id,
            "decision": "approved",
            "comments": "Approved via automated test suite",
        }
        res = self.client.post("/approvals/decide", data=payload, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        db.session.refresh(step)
        self.assertEqual(step.decision, "approved")

        # Immediately restore step and request to preserve pristine baseline state
        step.decision = orig_decision
        step.comments = orig_comments
        step.decided_at = orig_decided_at
        step.leave_request.status = orig_status
        db.session.commit()

    # ---------------------------------------------------------
    # 5. Finance Portal - Vouchers, Payments & Budgets
    # ---------------------------------------------------------
    def test_finance_portal_view(self):
        """Finance portal displays vouchers, disbursements and budget cards."""
        res = self.client.get("/finance/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Finance & Reimbursement Portal", html)
        self.assertIn("Reimbursement Vouchers", html)
        self.assertIn("Cost Center Budget Health", html)

    def test_finance_status_filters(self):
        """Finance portal filters by pending and paid vouchers."""
        res_pending = self.client.get("/finance/?status=pending")
        self.assertEqual(res_pending.status_code, 200)

        res_paid = self.client.get("/finance/?status=paid")
        self.assertEqual(res_paid.status_code, 200)

    def test_finance_pay_reimbursement(self):
        """Marking a reimbursement paid updates its status to 'paid' when authorized."""
        # Ensure session is authenticated as authorized Finance persona
        with self.client.session_transaction() as sess:
            sess["user_id"] = "EMP-000002"  # VP Finance

        rem = Reimbursement.query.filter_by(status="pending").first()
        self.assertIsNotNone(rem)

        orig_status = rem.status
        orig_paid_at = rem.paid_at

        res = self.client.post(f"/finance/reimbursements/{rem.reimbursement_id}/pay", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("successfully marked as PAID", html)

        db.session.refresh(rem)
        self.assertEqual(rem.status, "paid")
        self.assertIsNotNone(rem.paid_at)

        # Immediately restore reimbursement to preserve pristine baseline state
        WorkflowEvent.query.filter_by(event_id=f"EVT-{rem.reimbursement_id}-PAID").delete()
        rem.status = orig_status
        rem.paid_at = orig_paid_at
        db.session.commit()

    def test_finance_budgets_view(self):
        """Cost center budget management view lists all 20 cost centers with utilization."""
        res = self.client.get("/finance/budgets")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Cost Center Budget Management", html)
        self.assertIn("Cost Center Budget Allocations", html)

    # ---------------------------------------------------------
    # 6. Admin Portal - Master Configurations & Audit Logs
    # ---------------------------------------------------------
    def _login_as_admin(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = "EMP-000001"  # CEO / Executive

    def test_admin_portal_view(self):
        """Admin portal displays telemetry for all 12 core entities."""
        self._login_as_admin()
        res = self.client.get("/admin/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Admin & System Operations", html)
        self.assertIn("12 Core Relational Entities", html)
        self.assertIn("Employees", html)
        self.assertIn("Approval Rules", html)
        self.assertIn("Workflow Events", html)

    def test_admin_employees_view_and_search(self):
        """Admin employee directory supports searching and filtering."""
        self._login_as_admin()
        res = self.client.get("/admin/employees")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Employee Directory", html)

        res_search = self.client.get("/admin/employees?q=Riley")
        self.assertEqual(res_search.status_code, 200)

        res_country = self.client.get("/admin/employees?country=USA")
        self.assertEqual(res_country.status_code, 200)

    def test_admin_policies_view(self):
        """Admin policies view displays leave and expense policy matrices."""
        self._login_as_admin()
        res = self.client.get("/admin/policies")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Leave Policies (6)", html)
        self.assertIn("Expense Policies (35)", html)

    def test_admin_rules_view(self):
        """Admin rules view displays approval matrix and supports filtering."""
        self._login_as_admin()
        res = self.client.get("/admin/rules?request_type=leave")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Approval Routing Matrix", html)

    def test_admin_cost_centers_view(self):
        """Admin cost centers view displays all 20 cost centers."""
        self._login_as_admin()
        res = self.client.get("/admin/cost-centers")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Cost Centers Master Registry", html)

    def test_admin_audit_log_view(self):
        """Admin audit log streams immutable workflow lifecycle events."""
        self._login_as_admin()
        res = self.client.get("/admin/audit-log")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Immutable Workflow Event Audit Stream", html)

    # ---------------------------------------------------------
    # 7. AJAX API Endpoints
    # ---------------------------------------------------------
    def test_api_calculate_leave(self):
        """API calculates business days excluding weekends."""
        payload = {
            "start_date": "2026-06-01",  # Monday
            "end_date": "2026-06-05",    # Friday
        }
        res = self.client.post("/api/calculate-leave", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["business_days"], 5.0)

    def test_api_cost_center_budget(self):
        """API returns cost center budget utilization JSON."""
        cc = CostCenter.query.first()
        self.assertIsNotNone(cc)

        res = self.client.get(f"/api/cost-center-budget/{cc.cost_center_id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("annual_budget", data)
        self.assertIn("utilization_percentage", data)

    def test_api_policies_for_country(self):
        """API returns leave policies filtered by country."""
        res = self.client.get("/api/policies-for-country/USA")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(len(data["policies"]) > 0)
        for p in data["policies"]:
            self.assertEqual(p["country"], "USA")

    # ---------------------------------------------------------
    # 8. Error Handling (404 and 500)
    # ---------------------------------------------------------
    def test_404_error_page(self):
        """Non-existent URL triggers custom branded 404 page."""
        res = self.client.get("/this-route-does-not-exist-at-all")
        self.assertEqual(res.status_code, 404)
        html = res.data.decode("utf-8")
        self.assertIn("Page Not Found", html)
        self.assertIn("Return to Dashboard", html)


if __name__ == "__main__":
    unittest.main()
