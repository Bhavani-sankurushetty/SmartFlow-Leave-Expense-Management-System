"""SQLAlchemy models for SmartFlow."""

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


def utc_now():
    return datetime.now(timezone.utc)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class CostCenter(db.Model):
    __tablename__ = "cost_centers"

    cost_center_id = db.Column(db.String(32), primary_key=True)
    code = db.Column(db.String(32), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    country = db.Column(db.String(64), nullable=False)
    annual_budget = db.Column(db.Numeric(14, 2), nullable=False)
    owner_employee_id = db.Column(
        db.String(16),
        db.ForeignKey("employees.employee_id"),
        nullable=True,
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    owner = db.relationship(
        "Employee",
        foreign_keys=[owner_employee_id],
        back_populates="owned_cost_centers",
    )
    employees = db.relationship(
        "Employee",
        foreign_keys="Employee.cost_center_id",
        back_populates="cost_center",
    )
    expense_claims = db.relationship("ExpenseClaim", back_populates="cost_center")


class Employee(db.Model):
    __tablename__ = "employees"

    employee_id = db.Column(db.String(16), primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    country = db.Column(db.String(64), nullable=False)
    department = db.Column(db.String(80), nullable=False)
    job_title = db.Column(db.String(120), nullable=False)
    manager_id = db.Column(
        db.String(16),
        db.ForeignKey("employees.employee_id"),
        nullable=True,
    )
    cost_center_id = db.Column(
        db.String(32),
        db.ForeignKey("cost_centers.cost_center_id"),
        nullable=True,
    )
    hire_date = db.Column(db.Date, nullable=False)
    termination_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    manager = db.relationship(
        "Employee",
        remote_side=[employee_id],
        foreign_keys=[manager_id],
        back_populates="direct_reports",
    )
    direct_reports = db.relationship(
        "Employee",
        foreign_keys=[manager_id],
        back_populates="manager",
    )
    cost_center = db.relationship(
        "CostCenter",
        foreign_keys=[cost_center_id],
        back_populates="employees",
    )
    owned_cost_centers = db.relationship(
        "CostCenter",
        foreign_keys="CostCenter.owner_employee_id",
        back_populates="owner",
    )
    leave_balances = db.relationship("LeaveBalance", back_populates="employee")
    leave_requests = db.relationship("LeaveRequest", back_populates="employee")
    expense_claims = db.relationship("ExpenseClaim", back_populates="employee")
    approval_steps = db.relationship("ApprovalStep", back_populates="approver")
    workflow_events = db.relationship(
        "WorkflowEvent",
        foreign_keys="WorkflowEvent.actor_employee_id",
        back_populates="actor",
    )

    @property
    def role(self) -> str:
        """Role categorization for display and access control."""
        if not self.manager_id:
            return "Executive"
        if self.direct_reports:
            return "Manager"
        return "Employee"


class LeavePolicy(db.Model):
    __tablename__ = "leave_policies"

    leave_policy_id = db.Column(db.String(32), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    leave_type = db.Column(db.String(64), nullable=False)
    country = db.Column(db.String(64), nullable=False)
    annual_allowance_days = db.Column(db.Numeric(8, 2), nullable=False)
    max_consecutive_days = db.Column(db.Numeric(8, 2), nullable=True)
    min_notice_days = db.Column(db.Integer, nullable=False, default=0)
    carryover_days = db.Column(db.Numeric(8, 2), nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    leave_balances = db.relationship("LeaveBalance", back_populates="leave_policy")
    leave_requests = db.relationship("LeaveRequest", back_populates="leave_policy")

    @property
    def category(self) -> str:
        return self.leave_type

    @property
    def annual_allowance(self):
        return self.annual_allowance_days

    @property
    def max_carryover_days(self):
        return self.carryover_days

    @property
    def advance_notice_days(self):
        return self.min_notice_days

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def is_paid(self) -> bool:
        return self.leave_type != "Unpaid"


class LeaveBalance(db.Model):
    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint(
            "employee_id",
            "leave_policy_id",
            "year",
            name="uq_leave_balance_employee_policy_year",
        ),
    )

    leave_balance_id = db.Column(db.String(32), primary_key=True)
    employee_id = db.Column(
        db.String(16),
        db.ForeignKey("employees.employee_id"),
        nullable=False,
    )
    leave_policy_id = db.Column(
        db.String(32),
        db.ForeignKey("leave_policies.leave_policy_id"),
        nullable=False,
    )
    year = db.Column(db.Integer, nullable=False)
    opening_balance = db.Column(db.Numeric(8, 2), nullable=False)
    accrued_days = db.Column(db.Numeric(8, 2), nullable=False, default=0)
    used_days = db.Column(db.Numeric(8, 2), nullable=False, default=0)
    remaining_days = db.Column(db.Numeric(8, 2), nullable=False)
    as_of_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    employee = db.relationship("Employee", back_populates="leave_balances")
    leave_policy = db.relationship("LeavePolicy", back_populates="leave_balances")


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"

    leave_request_id = db.Column(db.String(32), primary_key=True)
    employee_id = db.Column(
        db.String(16),
        db.ForeignKey("employees.employee_id"),
        nullable=False,
    )
    leave_policy_id = db.Column(
        db.String(32),
        db.ForeignKey("leave_policies.leave_policy_id"),
        nullable=False,
    )
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    business_days = db.Column(db.Numeric(8, 2), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="submitted")
    submitted_at = db.Column(db.DateTime, nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    employee = db.relationship("Employee", back_populates="leave_requests")
    leave_policy = db.relationship("LeavePolicy", back_populates="leave_requests")
    approval_steps = db.relationship("ApprovalStep", back_populates="leave_request")


class ExpensePolicy(db.Model):
    __tablename__ = "expense_policies"

    expense_policy_id = db.Column(db.String(32), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(64), nullable=False)
    country = db.Column(db.String(64), nullable=False)
    max_amount = db.Column(db.Numeric(14, 2), nullable=False)
    receipt_required_above = db.Column(db.Numeric(14, 2), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    expense_claims = db.relationship("ExpenseClaim", back_populates="expense_policy")

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def max_single_transaction(self):
        return self.max_amount

    @property
    def daily_limit(self):
        return None


class ExpenseClaim(db.Model):
    __tablename__ = "expense_claims"

    expense_claim_id = db.Column(db.String(32), primary_key=True)
    employee_id = db.Column(
        db.String(16),
        db.ForeignKey("employees.employee_id"),
        nullable=False,
    )
    cost_center_id = db.Column(
        db.String(32),
        db.ForeignKey("cost_centers.cost_center_id"),
        nullable=True,
    )
    expense_policy_id = db.Column(
        db.String(32),
        db.ForeignKey("expense_policies.expense_policy_id"),
        nullable=False,
    )
    claim_date = db.Column(db.Date, nullable=False)
    country = db.Column(db.String(64), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="USD")
    total_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    status = db.Column(db.String(32), nullable=False, default="submitted")
    submitted_at = db.Column(db.DateTime, nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    employee = db.relationship("Employee", back_populates="expense_claims")
    cost_center = db.relationship("CostCenter", back_populates="expense_claims")
    expense_policy = db.relationship("ExpensePolicy", back_populates="expense_claims")
    expense_lines = db.relationship(
        "ExpenseLine",
        back_populates="expense_claim",
        cascade="all, delete-orphan",
    )
    approval_steps = db.relationship("ApprovalStep", back_populates="expense_claim")
    reimbursements = db.relationship("Reimbursement", back_populates="expense_claim")


class ExpenseLine(db.Model):
    __tablename__ = "expense_lines"

    expense_line_id = db.Column(db.String(64), primary_key=True)
    expense_claim_id = db.Column(
        db.String(32),
        db.ForeignKey("expense_claims.expense_claim_id"),
        nullable=False,
    )
    line_number = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(64), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    expense_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    country = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    expense_claim = db.relationship("ExpenseClaim", back_populates="expense_lines")


class ApprovalRule(db.Model):
    __tablename__ = "approval_rules"

    approval_rule_id = db.Column(db.String(32), primary_key=True)
    request_type = db.Column(db.String(32), nullable=False)
    country = db.Column(db.String(64), nullable=True)
    category = db.Column(db.String(64), nullable=True)
    min_amount = db.Column(db.Numeric(14, 2), nullable=True)
    max_amount = db.Column(db.Numeric(14, 2), nullable=True)
    authority_level = db.Column(db.Integer, nullable=False, default=1)
    step_order = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    approval_steps = db.relationship("ApprovalStep", back_populates="approval_rule")

    @property
    def threshold_min(self):
        return self.min_amount

    @property
    def threshold_max(self):
        return self.max_amount

    @property
    def is_auto_approved(self) -> bool:
        return self.authority_level == 0


class ApprovalStep(db.Model):
    """Append-only approval history. Rows are not updated after insert."""

    __tablename__ = "approval_steps"
    __table_args__ = (
        CheckConstraint(
            "(leave_request_id IS NOT NULL AND expense_claim_id IS NULL) OR "
            "(leave_request_id IS NULL AND expense_claim_id IS NOT NULL)",
            name="ck_approval_step_single_request",
        ),
    )

    approval_step_id = db.Column(db.String(64), primary_key=True)
    leave_request_id = db.Column(
        db.String(32),
        db.ForeignKey("leave_requests.leave_request_id"),
        nullable=True,
    )
    expense_claim_id = db.Column(
        db.String(32),
        db.ForeignKey("expense_claims.expense_claim_id"),
        nullable=True,
    )
    approval_rule_id = db.Column(
        db.String(32),
        db.ForeignKey("approval_rules.approval_rule_id"),
        nullable=True,
    )
    approver_employee_id = db.Column(
        db.String(16),
        db.ForeignKey("employees.employee_id"),
        nullable=False,
    )
    step_order = db.Column(db.Integer, nullable=False)
    decision = db.Column(db.String(32), nullable=False, default="pending")
    comments = db.Column(db.String(500), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    leave_request = db.relationship("LeaveRequest", back_populates="approval_steps")
    expense_claim = db.relationship("ExpenseClaim", back_populates="approval_steps")
    approval_rule = db.relationship("ApprovalRule", back_populates="approval_steps")
    approver = db.relationship("Employee", back_populates="approval_steps")

    @property
    def authority_level(self) -> int:
        if self.approval_rule and self.approval_rule.authority_level:
            return self.approval_rule.authority_level
        return self.step_order


class Reimbursement(db.Model):
    __tablename__ = "reimbursements"
    __table_args__ = (
        UniqueConstraint(
            "expense_claim_id",
            name="uq_reimbursement_expense_claim",
        ),
    )

    reimbursement_id = db.Column(db.String(32), primary_key=True)
    expense_claim_id = db.Column(
        db.String(32),
        db.ForeignKey("expense_claims.expense_claim_id"),
        nullable=False,
        unique=True,
    )
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending")
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    expense_claim = db.relationship("ExpenseClaim", back_populates="reimbursements")

    @property
    def payment_method(self) -> str:
        return "direct_deposit"


class WorkflowEvent(db.Model):
    """Append-only audit trail for workflow state transitions and lifecycle events."""

    __tablename__ = "workflow_events"

    event_id = db.Column(db.String(64), primary_key=True)
    request_type = db.Column(db.String(32), nullable=False)
    entity_id = db.Column(db.String(32), nullable=False)
    event_type = db.Column(db.String(64), nullable=False)
    actor_employee_id = db.Column(
        db.String(16),
        db.ForeignKey("employees.employee_id"),
        nullable=True,
    )
    from_status = db.Column(db.String(32), nullable=True)
    to_status = db.Column(db.String(32), nullable=True)
    payload_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    actor = db.relationship("Employee", foreign_keys=[actor_employee_id], back_populates="workflow_events")

    @property
    def timestamp(self):
        return self.created_at

    @property
    def entity_type(self):
        return self.request_type

    @property
    def details(self):
        return self.payload_json or f"{self.from_status or 'start'} -> {self.to_status}"
