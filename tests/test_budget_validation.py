"""Unit tests for Phase 9 Cost Center Budget Validation."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from app import app
from models import CostCenter, Employee, ExpenseClaim, ExpenseLine, ExpensePolicy, db
from services.expense_service import (
    evaluate_expense_claim,
    get_cost_center_budget_utilization,
    money,
)


class TestBudgetValidation(unittest.TestCase):
    """Test suite covering budget-aware expense validation, currency rules, and limits."""

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

        # Create isolated test cost center and employee
        self.cc = CostCenter(
            cost_center_id="CC-TBV-001",
            code="CC-USA-TBV-001",
            name="US Engineering Test",
            country="USA",
            annual_budget=Decimal("10000.00"),
            is_active=True,
        )
        self.pol = ExpensePolicy(
            expense_policy_id="EPOL-TBV-001",
            name="Travel Policy Test",
            category="Travel",
            country="USA",
            max_amount=Decimal("50000.00"),
            receipt_required_above=Decimal("999999.00"),  # high limit so receipt check doesn't interfere with budget tests
            is_active=True,
        )
        db.session.add(self.cc)
        db.session.add(self.pol)
        db.session.flush()

        self.emp = Employee(
            employee_id="EMP-TBV-001",
            first_name="Alice",
            last_name="Smith",
            email="alice.smith.tbv@example.com",
            country="USA",
            department="Engineering",
            job_title="Software Engineer",
            cost_center_id="CC-TBV-001",
            hire_date=date(2025, 1, 1),
            is_active=True,
        )
        db.session.add(self.emp)
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        self._cleanup_test_records()

    def _cleanup_test_records(self):
        try:
            ExpenseLine.query.filter(ExpenseLine.expense_claim_id.like("EC-TBV-%")).delete(synchronize_session=False)
            ExpenseClaim.query.filter(ExpenseClaim.expense_claim_id.like("EC-TBV-%")).delete(synchronize_session=False)
            Employee.query.filter_by(employee_id="EMP-TBV-001").delete(synchronize_session=False)
            CostCenter.query.filter_by(cost_center_id="CC-TBV-001").delete(synchronize_session=False)
            CostCenter.query.filter_by(cost_center_id="CC-TBV-CAN").delete(synchronize_session=False)
            ExpensePolicy.query.filter_by(expense_policy_id="EPOL-TBV-001").delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _create_claim_with_lines(
        self,
        claim_id: str,
        amount: Decimal,
        status: str = "submitted",
        year: int = 2026,
        currency: str = "USD",
        country: str = "USA",
    ) -> ExpenseClaim:
        c = ExpenseClaim(
            expense_claim_id=claim_id,
            employee_id=self.emp.employee_id,
            cost_center_id=self.cc.cost_center_id,
            expense_policy_id=self.pol.expense_policy_id,
            claim_date=date(year, 6, 1),
            country=country,
            currency=currency,
            total_amount=amount,
            status=status,
        )
        db.session.add(c)
        db.session.flush()

        line = ExpenseLine(
            expense_line_id=f"EXPL-{claim_id}-01",
            expense_claim_id=claim_id,
            line_number=1,
            category="Travel",
            description=f"Test line for {claim_id}",
            expense_date=date(year, 5, 20),
            amount=amount,
            country=country,
        )
        db.session.add(line)
        db.session.commit()
        return c

    def test_01_budget_utilization_calculation(self):
        """1. Utilization calculates used budget strictly from finalized approved claims."""
        self._create_claim_with_lines("EC-TBV-001", Decimal("3000.00"), status="approved")
        self._create_claim_with_lines("EC-TBV-002", Decimal("2000.00"), status="submitted")
        self._create_claim_with_lines("EC-TBV-003", Decimal("1500.00"), status="rejected")

        util = get_cost_center_budget_utilization("CC-TBV-001", year=2026)
        self.assertEqual(util["cost_center_id"], "CC-TBV-001")
        self.assertEqual(util["annual_budget"], Decimal("10000.00"))
        self.assertEqual(util["budget_used"], Decimal("3000.00"))
        self.assertEqual(util["budget_remaining"], Decimal("7000.00"))
        self.assertEqual(util["utilization_percentage"], Decimal("30.00"))
        self.assertEqual(util["approved_claims_count"], 1)

    def test_02_budget_utilization_excludes_other_years(self):
        """2. Budget utilization only counts approved claims within the target year."""
        self._create_claim_with_lines("EC-TBV-004", Decimal("4000.00"), status="approved", year=2025)
        self._create_claim_with_lines("EC-TBV-005", Decimal("1000.00"), status="approved", year=2026)

        util = get_cost_center_budget_utilization("CC-TBV-001", year=2026)
        self.assertEqual(util["budget_used"], Decimal("1000.00"))
        self.assertEqual(util["budget_remaining"], Decimal("9000.00"))

    def test_03_claim_exceeds_available_budget(self):
        """3. Detect claim that exceeds available remaining budget."""
        self._create_claim_with_lines("EC-TBV-006", Decimal("8000.00"), status="approved")
        c2 = self._create_claim_with_lines("EC-TBV-007", Decimal("3000.00"), status="submitted")

        eval_res = evaluate_expense_claim(c2.expense_claim_id)
        self.assertEqual(eval_res["decision"], "REJECT")
        self.assertEqual(eval_res["reason_code"], "BUDGET_EXCEEDED")
        self.assertEqual(eval_res["budget_used"], Decimal("8000.00"))
        self.assertEqual(eval_res["budget_remaining"], Decimal("2000.00"))
        self.assertEqual(eval_res["requested_amount"], Decimal("3000.00"))
        self.assertIn("exceeds available remaining budget", eval_res["reason"])

    def test_04_cost_center_budget_exhausted(self):
        """4. Detect when cost center budget is 100% exhausted."""
        self._create_claim_with_lines("EC-TBV-008", Decimal("10000.00"), status="approved")
        c2 = self._create_claim_with_lines("EC-TBV-009", Decimal("500.00"), status="submitted")

        eval_res = evaluate_expense_claim(c2.expense_claim_id)
        self.assertEqual(eval_res["decision"], "REJECT")
        self.assertEqual(eval_res["reason_code"], "BUDGET_EXHAUSTED")
        self.assertEqual(eval_res["budget_remaining"], Decimal("0.00"))
        self.assertEqual(eval_res["utilization_percentage"], Decimal("100.00"))

    def test_05_claim_within_budget_eligible(self):
        """5. Claim within available budget passes budget validation."""
        self._create_claim_with_lines("EC-TBV-010", Decimal("4000.00"), status="approved")
        c2 = self._create_claim_with_lines("EC-TBV-011", Decimal("2000.00"), status="submitted")

        eval_res = evaluate_expense_claim(c2.expense_claim_id)
        self.assertNotEqual(eval_res["decision"], "REJECT")
        self.assertEqual(eval_res["decision"], "NEEDS_REVIEW")
        self.assertEqual(eval_res["reason_code"], "ELIGIBLE_FOR_APPROVAL")
        self.assertEqual(eval_res["budget_used"], Decimal("4000.00"))
        self.assertEqual(eval_res["budget_remaining"], Decimal("6000.00"))

    def test_06_non_mutating_evaluation(self):
        """6. Evaluating budget must NOT mutate database record status."""
        c = self._create_claim_with_lines("EC-TBV-012", Decimal("15000.00"), status="submitted")
        eval_res = evaluate_expense_claim(c.expense_claim_id)
        self.assertEqual(eval_res["decision"], "REJECT")

        db.session.refresh(c)
        self.assertEqual(c.status, "submitted")

    def test_07_country_cost_center_mismatch(self):
        """7. Verify employee, claim, and cost center countries are consistent."""
        foreign_cc = CostCenter(
            cost_center_id="CC-TBV-CAN",
            code="CC-CAN-ENG-TBV",
            name="Canada Engineering",
            country="Canada",
            annual_budget=Decimal("50000.00"),
            is_active=True,
        )
        db.session.add(foreign_cc)
        db.session.commit()

        c = ExpenseClaim(
            expense_claim_id="EC-TBV-013",
            employee_id=self.emp.employee_id,  # USA employee
            cost_center_id=foreign_cc.cost_center_id,  # Canada CC
            expense_policy_id=self.pol.expense_policy_id,
            claim_date=date(2026, 6, 1),
            country="USA",
            currency="USD",
            total_amount=Decimal("500.00"),
            status="submitted",
        )
        db.session.add(c)
        db.session.commit()

        line = ExpenseLine(
            expense_line_id="EXPL-EC-TBV-013-01",
            expense_claim_id=c.expense_claim_id,
            line_number=1,
            category="Travel",
            description="Test line",
            expense_date=date(2026, 5, 20),
            amount=Decimal("500.00"),
            country="USA",
        )
        db.session.add(line)
        db.session.commit()

        eval_res = evaluate_expense_claim(c.expense_claim_id)
        self.assertEqual(eval_res["decision"], "REJECT")
        self.assertEqual(eval_res["reason_code"], "COUNTRY_COST_CENTER_MISMATCH")

        ExpenseLine.query.filter_by(expense_line_id="EXPL-EC-TBV-013-01").delete()
        ExpenseClaim.query.filter_by(expense_claim_id="EC-TBV-013").delete()
        CostCenter.query.filter_by(cost_center_id="CC-TBV-CAN").delete()
        db.session.commit()

    def test_08_currency_mismatch_detected(self):
        """8. Currency mismatch against cost center country currency is rejected."""
        c = self._create_claim_with_lines("EC-TBV-014", Decimal("500.00"), currency="EUR")
        eval_res = evaluate_expense_claim(c.expense_claim_id)
        self.assertEqual(eval_res["decision"], "REJECT")
        self.assertEqual(eval_res["reason_code"], "CURRENCY_MISMATCH")

    def test_09_re_evaluation_excludes_self(self):
        """9. Re-evaluating an approved claim excludes itself from used budget."""
        c = self._create_claim_with_lines("EC-TBV-015", Decimal("9000.00"), status="approved")

        util_exclude = get_cost_center_budget_utilization("CC-TBV-001", year=2026, exclude_claim_id="EC-TBV-015")
        self.assertEqual(util_exclude["budget_used"], Decimal("0.00"))
        self.assertEqual(util_exclude["budget_remaining"], Decimal("10000.00"))


if __name__ == "__main__":
    unittest.main()
