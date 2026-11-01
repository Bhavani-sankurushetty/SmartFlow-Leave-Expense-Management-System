"""Unit tests for SmartFlow Phase 5 Leave Service."""

import unittest
from datetime import date
from decimal import Decimal

from app import app
from models import Employee, LeavePolicy, LeaveRequest
from services.leave_service import (
    calculate_business_days,
    evaluate_leave_request,
    get_available_leave_balance,
)


class TestLeaveService(unittest.TestCase):
    """Test suite covering the 9 required leave evaluation scenarios."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()

    def test_01_valid_leave_request(self):
        """1. A valid leave request returns ELIGIBLE_FOR_APPROVAL and NEEDS_REVIEW."""
        emp = Employee.query.filter_by(country="India").first()
        self.assertIsNotNone(emp, "Must have an Indian employee in database.")

        req = {
            "request_id": "TEST-REQ-01",
            "employee_id": emp.employee_id,
            "leave_policy_id": "LPOL-000001",  # India Annual Leave
            "start_date": date(2026, 11, 2),   # Monday
            "end_date": date(2026, 11, 3),     # Tuesday (2 days)
            "status": "submitted",
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "NEEDS_REVIEW")
        self.assertEqual(res["reason_code"], "ELIGIBLE_FOR_APPROVAL")
        self.assertEqual(res["requested_days"], Decimal("2.00"))
        self.assertGreater(res["available_days"], Decimal("0.00"))

    def test_02_insufficient_leave_balance(self):
        """2. A request whose requested business days exceed available balance returns BALANCE_EXCEEDED."""
        emp = Employee.query.filter_by(country="India").first()
        avail, _ = get_available_leave_balance(emp.employee_id, "LPOL-000001")

        # Request 14 business days (within policy max of 15, but exceeds avail of 12)
        req = {
            "request_id": "TEST-REQ-02",
            "employee_id": emp.employee_id,
            "leave_policy_id": "LPOL-000001",
            "start_date": date(2026, 11, 2),
            "end_date": date(2026, 11, 20),
            "status": "submitted",
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "BALANCE_EXCEEDED")
        self.assertGreater(res["requested_days"], res["available_days"])

    def test_03_invalid_date_range(self):
        """3. A request with start_date after end_date returns INVALID_DATE_RANGE."""
        emp = Employee.query.filter_by(country="India").first()
        req = {
            "request_id": "TEST-REQ-03",
            "employee_id": emp.employee_id,
            "leave_policy_id": "LPOL-000001",
            "start_date": date(2026, 11, 10),
            "end_date": date(2026, 11, 5),
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "INVALID_DATE_RANGE")

    def test_04_employee_not_found(self):
        """4. A request for a non-existent employee returns EMPLOYEE_NOT_FOUND."""
        req = {
            "request_id": "TEST-REQ-04",
            "employee_id": "EMP-999999",
            "leave_policy_id": "LPOL-000001",
            "start_date": date(2026, 11, 2),
            "end_date": date(2026, 11, 3),
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "EMPLOYEE_NOT_FOUND")

    def test_05_policy_country_mismatch(self):
        """5. A request where employee country does not match policy country returns COUNTRY_POLICY_MISMATCH."""
        emp = Employee.query.filter_by(country="India").first()
        req = {
            "request_id": "TEST-REQ-05",
            "employee_id": emp.employee_id,    # India
            "leave_policy_id": "LPOL-000002",  # USA Sick Leave
            "start_date": date(2026, 11, 2),
            "end_date": date(2026, 11, 3),
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "COUNTRY_POLICY_MISMATCH")

    def test_06_overlapping_approved_leave(self):
        """6. A request overlapping an already approved leave for that employee returns OVERLAPPING_APPROVED_LEAVE."""
        approved_req = LeaveRequest.query.filter_by(status="approved").first()
        self.assertIsNotNone(approved_req, "Must have an approved leave request in database.")

        req = {
            "request_id": "TEST-REQ-06",
            "employee_id": approved_req.employee_id,
            "leave_policy_id": approved_req.leave_policy_id,
            "start_date": approved_req.start_date,
            "end_date": approved_req.end_date,
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "OVERLAPPING_APPROVED_LEAVE")

    def test_07_leave_outside_employment_period(self):
        """7. A request with dates prior to hire date returns OUTSIDE_EMPLOYMENT_PERIOD."""
        emp = Employee.query.filter_by(country="India").first()
        req = {
            "request_id": "TEST-REQ-07",
            "employee_id": emp.employee_id,
            "leave_policy_id": "LPOL-000001",  # India Annual Leave
            "start_date": date(2010, 1, 1),
            "end_date": date(2010, 1, 5),
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "OUTSIDE_EMPLOYMENT_PERIOD")

    def test_08_policy_limit_exceeded(self):
        """8. A request exceeding policy maximum consecutive days returns POLICY_LIMIT_EXCEEDED."""
        emp = Employee.query.filter_by(country="UK").first()
        self.assertIsNotNone(emp, "Must have a UK employee.")
        # LPOL-000003 is UK Casual Leave, max_consecutive_days = 3.00
        req = {
            "request_id": "TEST-REQ-08",
            "employee_id": emp.employee_id,
            "leave_policy_id": "LPOL-000003",
            "start_date": date(2026, 11, 2),  # Monday
            "end_date": date(2026, 11, 6),    # Friday (5 business days > 3 max)
        }
        res = evaluate_leave_request(req)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "POLICY_LIMIT_EXCEEDED")
        self.assertEqual(res["requested_days"], Decimal("5.00"))
        self.assertEqual(res["policy_limit"], Decimal("3.00"))

    def test_09_repeated_evaluation_gives_same_result(self):
        """9. Repeated evaluation returns identical results without modifying the database."""
        emp = Employee.query.filter_by(country="India").first()
        req = {
            "request_id": "TEST-REQ-09",
            "employee_id": emp.employee_id,
            "leave_policy_id": "LPOL-000001",
            "start_date": date(2026, 11, 2),
            "end_date": date(2026, 11, 3),
            "status": "submitted",
        }
        res1 = evaluate_leave_request(req)
        res2 = evaluate_leave_request(req)

        self.assertEqual(res1["decision"], res2["decision"])
        self.assertEqual(res1["reason_code"], res2["reason_code"])
        self.assertEqual(res1["requested_days"], res2["requested_days"])
        self.assertEqual(res1["available_days"], res2["available_days"])
        self.assertEqual(res1["policy_limit"], res2["policy_limit"])


if __name__ == "__main__":
    unittest.main()
