"""Access control, role-based authorization, and security tests for SmartFlow."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from app import app
from config import DATABASE_PATH, Config
from models import Employee, Reimbursement, WorkflowEvent, db
from sqlalchemy import create_engine


class TestAccessControlAndSecurity(unittest.TestCase):
    """Isolated security and access control tests using a temporary database clone."""

    @classmethod
    def setUpClass(cls):
        # Create an isolated temporary copy of the V2 database to guarantee zero mutation of submitted databases
        cls.temp_dir = tempfile.mkdtemp()
        cls.temp_db_path = os.path.join(cls.temp_dir, "test_security.db")
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

    # -------------------------------------------------------------------------
    # 1. Finance Route Authorization (POST /finance/reimbursements/<id>/pay)
    # -------------------------------------------------------------------------
    def test_unauthorized_staff_employee_cannot_pay_reimbursement(self):
        """Staff employees (non-Finance, non-Executive) are blocked from disbursing payments."""
        staff = Employee.query.filter_by(employee_id="EMP-000080").first()
        self.assertIsNotNone(staff)
        self.assertEqual(staff.role, "Employee")
        self.assertNotEqual(staff.department, "Finance")

        with self.client.session_transaction() as sess:
            sess["user_id"] = staff.employee_id

        pending_rem = Reimbursement.query.filter_by(status="pending").first()
        self.assertIsNotNone(pending_rem)
        rem_id = pending_rem.reimbursement_id

        # Attempt disbursement as staff employee
        res = self.client.post(f"/finance/reimbursements/{rem_id}/pay", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Unauthorized: Only authorized Finance personnel or Executives", html)

        # Verify state in database was NOT altered
        db.session.expire_all()
        rem_after = Reimbursement.query.filter_by(reimbursement_id=rem_id).first()
        self.assertEqual(rem_after.status, "pending")
        self.assertIsNone(rem_after.paid_at)

        # Verify no paid event was written
        evt = WorkflowEvent.query.filter_by(event_id=f"EVT-{rem_id}-PAID").first()
        self.assertIsNone(evt)

    def test_unauthorized_sales_manager_cannot_pay_reimbursement(self):
        """Managers in non-Finance departments cannot disburse payments."""
        mgr = Employee.query.filter_by(department="Sales", is_active=True).first()
        self.assertIsNotNone(mgr)
        self.assertNotEqual(mgr.department, "Finance")
        self.assertNotEqual(mgr.role, "Executive")

        with self.client.session_transaction() as sess:
            sess["user_id"] = mgr.employee_id

        pending_rem = Reimbursement.query.filter_by(status="pending").first()
        rem_id = pending_rem.reimbursement_id

        res = self.client.post(f"/finance/reimbursements/{rem_id}/pay", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Unauthorized: Only authorized Finance personnel or Executives", html)

        db.session.expire_all()
        rem_after = Reimbursement.query.filter_by(reimbursement_id=rem_id).first()
        self.assertEqual(rem_after.status, "pending")

    def test_authorized_finance_persona_can_pay_reimbursement(self):
        """Authorized Finance personnel (e.g. VP Finance EMP-000002) can disburse payments."""
        finance_emp = Employee.query.filter_by(employee_id="EMP-000002").first()
        self.assertIsNotNone(finance_emp)
        self.assertEqual(finance_emp.department, "Finance")

        with self.client.session_transaction() as sess:
            sess["user_id"] = finance_emp.employee_id

        pending_rem = Reimbursement.query.filter_by(status="pending").first()
        self.assertIsNotNone(pending_rem)
        rem_id = pending_rem.reimbursement_id

        res = self.client.post(f"/finance/reimbursements/{rem_id}/pay", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("successfully marked as PAID", html)

        db.session.expire_all()
        rem_after = Reimbursement.query.filter_by(reimbursement_id=rem_id).first()
        self.assertEqual(rem_after.status, "paid")
        self.assertIsNotNone(rem_after.paid_at)

    def test_authorized_executive_persona_can_pay_reimbursement(self):
        """Executive persona (e.g. CEO EMP-000001) has administrative payment authorization."""
        ceo = Employee.query.filter_by(employee_id="EMP-000001").first()
        self.assertIsNotNone(ceo)
        self.assertEqual(ceo.role, "Executive")

        with self.client.session_transaction() as sess:
            sess["user_id"] = ceo.employee_id

        # Find another pending reimbursement
        pending_rem = Reimbursement.query.filter_by(status="pending").first()
        self.assertIsNotNone(pending_rem)
        rem_id = pending_rem.reimbursement_id

        res = self.client.post(f"/finance/reimbursements/{rem_id}/pay", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("successfully marked as PAID", html)

        db.session.expire_all()
        rem_after = Reimbursement.query.filter_by(reimbursement_id=rem_id).first()
        self.assertEqual(rem_after.status, "paid")

    # -------------------------------------------------------------------------
    # 2. Admin Portal Route Authorization (/admin/*)
    # -------------------------------------------------------------------------
    def test_unauthorized_staff_cannot_access_admin_portal(self):
        """Staff employee is redirected away from /admin/ with an Unauthorized warning."""
        staff = Employee.query.filter_by(employee_id="EMP-000080").first()
        with self.client.session_transaction() as sess:
            sess["user_id"] = staff.employee_id

        res = self.client.get("/admin/", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Unauthorized: Administrative directory access is restricted", html)
        self.assertIn("Operational Workflow Dashboard", html)  # redirected to dashboard

    def test_unauthorized_staff_cannot_access_admin_subroutes(self):
        """Staff employee cannot access administrative sub-endpoints."""
        staff = Employee.query.filter_by(employee_id="EMP-000080").first()
        with self.client.session_transaction() as sess:
            sess["user_id"] = staff.employee_id

        subroutes = [
            "/admin/employees",
            "/admin/policies",
            "/admin/rules",
            "/admin/cost-centers",
            "/admin/audit-log",
        ]
        for path in subroutes:
            res = self.client.get(path, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            html = res.data.decode("utf-8")
            self.assertIn("Unauthorized: Administrative directory access is restricted", html)

    def test_unauthorized_department_manager_cannot_access_admin(self):
        """Regional department manager (non-Executive, non-Operations) cannot access admin."""
        mgr = Employee.query.filter_by(department="Sales", is_active=True).first()
        self.assertNotEqual(mgr.role, "Executive")
        self.assertNotEqual(mgr.department, "Operations")

        with self.client.session_transaction() as sess:
            sess["user_id"] = mgr.employee_id

        res = self.client.get("/admin/", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Unauthorized: Administrative directory access is restricted", html)

    def test_authorized_executive_can_access_admin(self):
        """Executive persona (CEO EMP-000001) can access administrative portal and subroutes."""
        ceo = Employee.query.filter_by(employee_id="EMP-000001").first()
        self.assertEqual(ceo.role, "Executive")

        with self.client.session_transaction() as sess:
            sess["user_id"] = ceo.employee_id

        res = self.client.get("/admin/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Admin & System Operations", html)
        self.assertIn("12 Core Relational Entities", html)

    def test_authorized_operations_persona_can_access_admin(self):
        """Operations personnel (VP Operations EMP-000006) can access administrative portal."""
        ops = Employee.query.filter_by(employee_id="EMP-000006").first()
        self.assertIsNotNone(ops)
        self.assertEqual(ops.department, "Operations")

        with self.client.session_transaction() as sess:
            sess["user_id"] = ops.employee_id

        res = self.client.get("/admin/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Admin & System Operations", html)

    # -------------------------------------------------------------------------
    # 3. Demo Persona Switcher Verification
    # -------------------------------------------------------------------------
    def test_demo_persona_switcher_behavior(self):
        """Demo persona switcher assigns identity in session and warns simulation mode."""
        res = self.client.post("/switch-user", data={"employee_id": "EMP-000080"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("[Demo Simulation] Switched active user to", html)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("user_id"), "EMP-000080")


if __name__ == "__main__":
    unittest.main()
