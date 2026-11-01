"""Unit tests for Phase 9 Reimbursement Service."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
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
    Reimbursement,
    WorkflowEvent,
    db,
)
from services.reimbursement_service import (
    ReimbursementError,
    create_reimbursement_for_approved_claim,
    get_reimbursement,
    get_reimbursement_history,
    mark_reimbursement_paid,
)


class TestReimbursementService(unittest.TestCase):
    """Test suite covering the 10 reimbursement business rules and edge scenarios."""

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

        # Find existing active employee with direct manager from master data
        self.emp = Employee.query.filter(
            Employee.manager_id.isnot(None),
            Employee.is_active.is_(True),
        ).first()
        self.assertIsNotNone(self.emp, "Active employee with manager must exist in master data.")
        self.mgr = Employee.query.filter_by(employee_id=self.emp.manager_id).first()
        self.assertIsNotNone(self.mgr, "Manager must exist.")

        self.cc = CostCenter.query.filter_by(cost_center_id=self.emp.cost_center_id).first()
        self.pol = ExpensePolicy.query.filter_by(country=self.emp.country).first()
        self.rule = ApprovalRule.query.filter_by(country=self.emp.country, request_type="expense").first()

    def tearDown(self):
        db.session.rollback()
        self._cleanup_test_records()

    def _cleanup_test_records(self):
        try:
            WorkflowEvent.query.filter(
                (WorkflowEvent.entity_id.like("EXP-TRMB-%")) | (WorkflowEvent.entity_id.like("RMB-%"))
            ).delete(synchronize_session=False)
            Reimbursement.query.filter(Reimbursement.expense_claim_id.like("EXP-TRMB-%")).delete(synchronize_session=False)
            ApprovalStep.query.filter(ApprovalStep.expense_claim_id.like("EXP-TRMB-%")).delete(synchronize_session=False)
            ExpenseLine.query.filter(ExpenseLine.expense_claim_id.like("EXP-TRMB-%")).delete(synchronize_session=False)
            ExpenseClaim.query.filter(ExpenseClaim.expense_claim_id.like("EXP-TRMB-%")).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _create_claim_fixture(
        self,
        claim_id: str,
        amount: Decimal,
        claim_status: str = "approved",
        step_decision: str = "approved",
    ) -> ExpenseClaim:
        c = ExpenseClaim(
            expense_claim_id=claim_id,
            employee_id=self.emp.employee_id,
            cost_center_id=self.cc.cost_center_id,
            expense_policy_id=self.pol.expense_policy_id,
            claim_date=date(2026, 5, 1),
            country=self.emp.country,
            currency=self.pol.country,  # placeholder
            total_amount=amount,
            status=claim_status,
        )
        # Fix currency from cost center country
        from generator.constants import COUNTRY_CURRENCIES
        c.currency = COUNTRY_CURRENCIES.get(self.emp.country, "USD")

        db.session.add(c)
        db.session.flush()

        line = ExpenseLine(
            expense_line_id=f"EXPL-{claim_id}-01",
            expense_claim_id=claim_id,
            line_number=1,
            category=self.pol.category,
            description="Test fixture line",
            expense_date=date(2026, 4, 25),
            amount=amount,
            country=self.emp.country,
        )
        db.session.add(line)

        step = ApprovalStep(
            approval_step_id=f"APR-{claim_id}-01",
            expense_claim_id=claim_id,
            approval_rule_id=self.rule.approval_rule_id if self.rule else "ARUL-000001",
            step_order=1,
            approver_employee_id=self.mgr.employee_id,
            decision=step_decision,
            decided_at=datetime.now(timezone.utc) if step_decision != "pending" else None,
        )
        db.session.add(step)
        db.session.commit()
        return c

    def test_01_create_reimbursement_success(self):
        """1. Create reimbursement for approved claim with all steps approved."""
        self._create_claim_fixture("EXP-TRMB-001", Decimal("750.50"))

        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-001")
        self.assertIsNotNone(reimb)
        self.assertTrue(reimb.reimbursement_id.startswith("RMB-2026-"))
        self.assertEqual(reimb.expense_claim_id, "EXP-TRMB-001")
        self.assertEqual(Decimal(str(reimb.amount)), Decimal("750.50"))
        self.assertEqual(reimb.status, "pending")
        self.assertIsNotNone(reimb.created_at)
        self.assertIsNone(reimb.paid_at)

    def test_02_reimbursement_requires_approved_claim(self):
        """2. Cannot reimburse pending or submitted claims."""
        self._create_claim_fixture("EXP-TRMB-002", Decimal("300.00"), claim_status="submitted")

        with self.assertRaises(ReimbursementError) as ctx:
            create_reimbursement_for_approved_claim("EXP-TRMB-002")
        self.assertEqual(ctx.exception.code, "CLAIM_NOT_APPROVED")

    def test_03_reimbursement_requires_approved_steps(self):
        """3. Cannot reimburse if any required approval step is pending."""
        self._create_claim_fixture(
            "EXP-TRMB-003",
            Decimal("300.00"),
            claim_status="approved",
            step_decision="pending",
        )

        with self.assertRaises(ReimbursementError) as ctx:
            create_reimbursement_for_approved_claim("EXP-TRMB-003")
        self.assertEqual(ctx.exception.code, "UNAPPROVED_STEPS_REMAIN")

    def test_04_reimbursement_amount_matches_claim_total(self):
        """4. Reimbursement amount exactly matches approved claim amount."""
        c = self._create_claim_fixture("EXP-TRMB-004", Decimal("1234.56"))

        reimb = create_reimbursement_for_approved_claim(c.expense_claim_id)
        self.assertEqual(Decimal(str(reimb.amount)), Decimal("1234.56"))

    def test_05_deterministic_reimbursement_id(self):
        """5. Generates deterministic ID matching convention 'RMB-YYYY-NNNNNN'."""
        self._create_claim_fixture("EXP-TRMB-005", Decimal("100.00"))
        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-005")

        import re
        pattern = r"^RMB-\d{4}-\d{6}$"
        self.assertTrue(re.match(pattern, reimb.reimbursement_id))

    def test_06_prevent_duplicate_reimbursement(self):
        """6. Prevent creating more than one reimbursement for the same claim."""
        self._create_claim_fixture("EXP-TRMB-006", Decimal("250.00"))
        create_reimbursement_for_approved_claim("EXP-TRMB-006")

        with self.assertRaises(ReimbursementError) as ctx:
            create_reimbursement_for_approved_claim("EXP-TRMB-006")
        self.assertEqual(ctx.exception.code, "DUPLICATE_REIMBURSEMENT")

    def test_07_mark_reimbursement_paid_success(self):
        """7. Transition reimbursement to paid status with valid timestamp."""
        self._create_claim_fixture("EXP-TRMB-007", Decimal("450.00"))
        created = datetime.now(timezone.utc) - timedelta(hours=2)
        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-007", created_at=created)

        paid_time = datetime.now(timezone.utc)
        paid = mark_reimbursement_paid(reimb.reimbursement_id, paid_at=paid_time)

        self.assertEqual(paid.status, "paid")
        self.assertIsNotNone(paid.paid_at)

    def test_08_cannot_pay_already_paid_reimbursement(self):
        """8. Do not mark a reimbursement paid more than once."""
        self._create_claim_fixture("EXP-TRMB-008", Decimal("150.00"))
        created = datetime.now(timezone.utc) - timedelta(hours=1)
        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-008", created_at=created)
        mark_reimbursement_paid(reimb.reimbursement_id)

        with self.assertRaises(ReimbursementError) as ctx:
            mark_reimbursement_paid(reimb.reimbursement_id)
        self.assertEqual(ctx.exception.code, "ALREADY_PAID")

    def test_09_payment_timestamp_before_creation_rejected(self):
        """9. Payment timestamp must be on or after creation timestamp."""
        self._create_claim_fixture("EXP-TRMB-009", Decimal("500.00"))
        created = datetime.now(timezone.utc)
        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-009", created_at=created)

        past_time = created - timedelta(days=1)
        with self.assertRaises(ReimbursementError) as ctx:
            mark_reimbursement_paid(reimb.reimbursement_id, paid_at=past_time)
        self.assertEqual(ctx.exception.code, "INVALID_PAYMENT_DATE")

    def test_10_audit_event_logged_on_creation_and_payment(self):
        """10. WorkflowEvent audit trail is recorded on creation and payment."""
        self._create_claim_fixture("EXP-TRMB-010", Decimal("350.00"))
        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-010")

        create_event = WorkflowEvent.query.filter_by(
            entity_id=reimb.reimbursement_id,
            event_type="REIMBURSEMENT_CREATED",
        ).first()
        self.assertIsNotNone(create_event)
        self.assertEqual(create_event.to_status, "pending")

        mark_reimbursement_paid(reimb.reimbursement_id)

        pay_event = WorkflowEvent.query.filter_by(
            entity_id=reimb.reimbursement_id,
            event_type="REIMBURSEMENT_PAID",
        ).first()
        self.assertIsNotNone(pay_event)
        self.assertEqual(pay_event.from_status, "pending")
        self.assertEqual(pay_event.to_status, "paid")

    def test_11_get_reimbursement(self):
        """11. get_reimbursement retrieves record by ID."""
        self._create_claim_fixture("EXP-TRMB-011", Decimal("600.00"))
        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-011")

        found = get_reimbursement(reimb.reimbursement_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.reimbursement_id, reimb.reimbursement_id)

    def test_12_get_reimbursement_history(self):
        """12. get_reimbursement_history returns full claim reimbursement records."""
        self._create_claim_fixture("EXP-TRMB-012", Decimal("800.00"))
        reimb = create_reimbursement_for_approved_claim("EXP-TRMB-012")

        history = get_reimbursement_history("EXP-TRMB-012")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reimbursement_id"], reimb.reimbursement_id)
        self.assertEqual(history[0]["amount"], Decimal("800.00"))

    def test_13_nonexistent_claim_raises_error(self):
        """13. Creating reimbursement for nonexistent claim raises error."""
        with self.assertRaises(ReimbursementError) as ctx:
            create_reimbursement_for_approved_claim("EXP-NONEXISTENT")
        self.assertEqual(ctx.exception.code, "CLAIM_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
