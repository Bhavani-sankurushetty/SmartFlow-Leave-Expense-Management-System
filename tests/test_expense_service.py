"""Unit tests for SmartFlow Phase 6 Expense Service."""

import re
import unittest
from datetime import date
from decimal import Decimal

from app import app
from models import CostCenter, Employee, ExpenseClaim, ExpenseLine, ExpensePolicy
from services.expense_service import evaluate_expense_claim


class TestExpenseService(unittest.TestCase):
    """Test suite covering the 14 required expense evaluation scenarios."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()

    def setUp(self):
        self.emp = Employee.query.filter_by(country="USA").first()
        self.assertIsNotNone(self.emp, "Must have a USA employee.")
        self.cc = CostCenter.query.filter_by(cost_center_id=self.emp.cost_center_id).first()
        self.assertIsNotNone(self.cc, "Must have an associated cost center.")
        self.pol = ExpensePolicy.query.filter_by(country="USA", category="Travel").first()
        self.assertIsNotNone(self.pol, "Must have a USA Travel policy.")

        # Baseline valid claim dictionary
        self.valid_claim = {
            "claim_id": "EXP-TEST-0001",
            "employee_id": self.emp.employee_id,
            "cost_center_id": self.cc.cost_center_id,
            "expense_policy_id": self.pol.expense_policy_id,
            "claim_date": date(2026, 6, 15),
            "country": "USA",
            "total_amount": Decimal("150.00"),
            "status": "submitted",
            "lines": [
                {
                    "expense_line_id": "EXPL-TEST-0001-01",
                    "line_number": 1,
                    "category": "Travel",
                    "expense_date": date(2026, 6, 14),
                    "amount": Decimal("150.00"),
                    "description": "Flight booking [Receipt: test_flight.pdf | SHA256: e3b0c44298fc1c149afbf4c8996fb924]",
                }
            ],
        }

    def test_01_valid_expense_claim(self):
        """1. A valid expense claim returns ELIGIBLE_FOR_APPROVAL and NEEDS_REVIEW."""
        res = evaluate_expense_claim(self.valid_claim)
        self.assertEqual(res["decision"], "NEEDS_REVIEW")
        self.assertEqual(res["reason_code"], "ELIGIBLE_FOR_APPROVAL")
        self.assertEqual(res["claim_amount"], Decimal("150.00"))
        self.assertEqual(res["category"], "Travel")

    def test_02_employee_not_found(self):
        """2. A claim referencing a non-existent employee returns EMPLOYEE_NOT_FOUND."""
        bad_claim = {**self.valid_claim, "employee_id": "EMP-999999"}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "EMPLOYEE_NOT_FOUND")

    def test_03_policy_not_found(self):
        """3. A claim referencing a non-existent policy returns POLICY_NOT_FOUND."""
        bad_claim = {**self.valid_claim, "expense_policy_id": "EPOL-999999"}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "POLICY_NOT_FOUND")

    def test_04_country_policy_mismatch(self):
        """4. A claim where policy country does not match employee country returns COUNTRY_POLICY_MISMATCH."""
        pol_in = ExpensePolicy.query.filter_by(country="India", category="Travel").first()
        bad_claim = {**self.valid_claim, "expense_policy_id": pol_in.expense_policy_id}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "COUNTRY_POLICY_MISMATCH")

    def test_05_category_policy_mismatch(self):
        """5. A claim where an expense line category does not match policy category returns CATEGORY_POLICY_MISMATCH."""
        pol_meals = ExpensePolicy.query.filter_by(country="USA", category="Meals").first()
        # Policy is Meals, but line category is Travel
        bad_claim = {**self.valid_claim, "expense_policy_id": pol_meals.expense_policy_id}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "CATEGORY_POLICY_MISMATCH")

    def test_06_invalid_cost_center(self):
        """6. A claim with an invalid cost center returns COST_CENTER_NOT_FOUND."""
        bad_claim = {**self.valid_claim, "cost_center_id": "CC-999999"}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "COST_CENTER_NOT_FOUND")

    def test_07_invalid_line_count(self):
        """7. A claim with 0 lines or >10 lines returns INVALID_LINE_COUNT."""
        bad_claim_empty = {**self.valid_claim, "lines": []}
        res1 = evaluate_expense_claim(bad_claim_empty)
        self.assertEqual(res1["decision"], "REJECT")
        self.assertEqual(res1["reason_code"], "INVALID_LINE_COUNT")

        # 11 lines
        eleven_lines = [
            {**self.valid_claim["lines"][0], "line_number": i, "amount": Decimal("10.00")}
            for i in range(1, 12)
        ]
        bad_claim_eleven = {**self.valid_claim, "lines": eleven_lines, "total_amount": Decimal("110.00")}
        res2 = evaluate_expense_claim(bad_claim_eleven)
        self.assertEqual(res2["decision"], "REJECT")
        self.assertEqual(res2["reason_code"], "INVALID_LINE_COUNT")

    def test_08_invalid_expense_amount(self):
        """8. A claim with a negative or zero line amount returns INVALID_EXPENSE_AMOUNT."""
        bad_lines = [{**self.valid_claim["lines"][0], "amount": Decimal("-25.00")}]
        bad_claim = {**self.valid_claim, "lines": bad_lines, "total_amount": Decimal("-25.00")}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "INVALID_EXPENSE_AMOUNT")

    def test_09_claim_total_mismatch(self):
        """9. A claim whose total does not equal the sum of lines returns CLAIM_TOTAL_MISMATCH."""
        bad_claim = {**self.valid_claim, "total_amount": Decimal("500.00")}  # Line is 150.00
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "CLAIM_TOTAL_MISMATCH")

    def test_10_policy_limit_exceeded(self):
        """10. A claim whose total exceeds the policy max amount returns POLICY_LIMIT_EXCEEDED."""
        limit = Decimal(str(self.pol.max_amount))
        exceed_amt = limit + Decimal("100.00")
        bad_lines = [{**self.valid_claim["lines"][0], "amount": exceed_amt}]
        bad_claim = {**self.valid_claim, "lines": bad_lines, "total_amount": exceed_amt}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "POLICY_LIMIT_EXCEEDED")

    def test_11_missing_required_receipt(self):
        """11. A line with amount >= receipt_required_above missing receipt metadata returns RECEIPT_REQUIRED."""
        # USA Travel receipt threshold is $90.00. Line amount is $150.00 without [Receipt: ...]
        no_rec_lines = [
            {
                "expense_line_id": "EXPL-TEST-0001-01",
                "line_number": 1,
                "category": "Travel",
                "expense_date": date(2026, 6, 14),
                "amount": Decimal("150.00"),
                "description": "Airline ticket booked online",  # Missing receipt metadata
            }
        ]
        bad_claim = {**self.valid_claim, "lines": no_rec_lines}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "RECEIPT_REQUIRED")

    def test_12_duplicate_receipt_hash(self):
        """12. A claim reusing a receipt hash already present in another claim returns DUPLICATE_RECEIPT."""
        existing_line = ExpenseLine.query.filter(ExpenseLine.description.like("%[Receipt:%")).first()
        self.assertIsNotNone(existing_line, "Must have an expense line with receipt in database.")

        match = re.search(r"SHA256:\s*([a-fA-F0-9]+)", existing_line.description)
        self.assertIsNotNone(match, "Receipt hash must match pattern.")
        dup_hash = match.group(1)

        dup_lines = [
            {
                "expense_line_id": "EXPL-TEST-0001-01",
                "line_number": 1,
                "category": "Travel",
                "expense_date": date(2026, 6, 14),
                "amount": Decimal("150.00"),
                "description": f"Flight tickets [Receipt: duplicate.pdf | SHA256: {dup_hash}]",
            }
        ]
        bad_claim = {**self.valid_claim, "lines": dup_lines}
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "DUPLICATE_RECEIPT")

    def test_13_expense_outside_employment_period(self):
        """13. An expense line dated before hire_date returns EXPENSE_DATE_OUTSIDE_EMPLOYMENT."""
        bad_lines = [
            {
                **self.valid_claim["lines"][0],
                "expense_date": date(2010, 1, 1),
            }
        ]
        bad_claim = {
            **self.valid_claim,
            "claim_date": date(2010, 1, 5),
            "lines": bad_lines,
        }
        res = evaluate_expense_claim(bad_claim)
        self.assertEqual(res["decision"], "REJECT")
        self.assertEqual(res["reason_code"], "EXPENSE_DATE_OUTSIDE_EMPLOYMENT")

    def test_14_repeated_evaluation_gives_same_result(self):
        """14. Repeated evaluation produces identical results without mutating database state."""
        r1 = evaluate_expense_claim(self.valid_claim)
        r2 = evaluate_expense_claim(self.valid_claim)

        for k in ["claim_id", "employee_id", "decision", "reason_code", "claim_amount", "policy_limit", "country", "category"]:
            self.assertEqual(r1[k], r2[k])

    def test_15_receipt_hashes_are_64_hex_chars(self):
        """15. All receipt hashes in the database contain exactly 64 hexadecimal characters."""
        pattern = re.compile(r"\[Receipt:\s*([^\|\s]+)\s*\|\s*SHA256:\s*([a-fA-F0-9]+)\]")
        lines = ExpenseLine.query.filter(ExpenseLine.description.like("%[Receipt:%")).all()
        self.assertEqual(len(lines), 5360, "Expected exactly 5,360 expense lines with receipts.")
        for line in lines:
            match = pattern.search(line.description)
            self.assertIsNotNone(match, f"Line {line.expense_line_id} description must match receipt pattern.")
            h = match.group(2)
            self.assertEqual(len(h), 64, f"Line {line.expense_line_id} receipt hash must be exactly 64 chars.")

    def test_16_eligible_claim_requires_approval_and_cannot_bypass(self):
        """16. Eligible claims return NEEDS_REVIEW; approval cannot be bypassed via auto_approve or approved DB status."""
        # Baseline valid claim returns NEEDS_REVIEW
        res1 = evaluate_expense_claim(self.valid_claim)
        self.assertEqual(res1["decision"], "NEEDS_REVIEW")
        self.assertEqual(res1["reason_code"], "ELIGIBLE_FOR_APPROVAL")

        # Passing auto_approve=True still returns NEEDS_REVIEW
        res2 = evaluate_expense_claim(self.valid_claim, auto_approve=True)
        self.assertEqual(res2["decision"], "NEEDS_REVIEW")
        self.assertEqual(res2["reason_code"], "ELIGIBLE_FOR_APPROVAL")

        # Evaluating an existing claim from DB with status == 'approved' still returns NEEDS_REVIEW for eligibility
        approved_claim = ExpenseClaim.query.filter_by(status="approved").first()
        self.assertIsNotNone(approved_claim, "Must have an approved claim in database.")
        res3 = evaluate_expense_claim(approved_claim.expense_claim_id)
        self.assertEqual(res3["decision"], "NEEDS_REVIEW")
        self.assertEqual(res3["reason_code"], "ELIGIBLE_FOR_APPROVAL")


if __name__ == "__main__":
    import re
    unittest.main()
