# Schema

SQLite database files:
- Production baseline: `instance/smartflow.db` (read-only baseline snapshot from Phases 1-8)
- Production backup: `instance/smartflow.db.bak` (backup snapshot)
- Phase 9 Workflow & Simulation: `instance/phase9_smartflow.db` (Phase 9 dataset with workflow simulation)
- Phase 9.1 Preserved Dataset: `instance/phase9_smartflow_v2.db` (Phase 9.1 dataset with country-matched leave requests, 738 eligible / 62 ineligible leaves, 2,965 approval steps, 656 reimbursements, and 13,649 workflow events)
- Final Clean Submission: `instance/phase9_smartflow_submission.db` (Default application database: pristine, deterministic generation with seed `20261022`, exactly 13,645 workflow events, zero test-generated phantom events, and 100% agreement between decided approval steps [2,543] and `STEP_DECIDED` events [2,543])

## Tables

### Master Entities (Phase 3)
- `cost_centers`: Department cost centers with annual budgets and owner employees.
- `employees`: Organizational hierarchy, reporting managers, job titles, and countries.
- `leave_policies`: Statutory and company leave policies by country.
- `leave_balances`: Baseline annual leave allowances, accruals, and remaining balances per employee.
- `expense_policies`: Categorical spending limits and receipt threshold policies by country.
- `approval_rules`: Tiered approval routing matrices by country, request type, category, and amount thresholds.

### Transaction Entities (Phase 4)
- `leave_requests`: Leave requests with business days calculation and status lifecycle.
- `expense_claims`: Multi-line expense claims with currency, cost center, and total amount.
- `expense_lines`: Individual expense line items with category, amount, date, and receipt metadata.

### Workflow & Lifecycle Entities (Phases 5-9)
- `approval_steps`: Sequential approval steps for leave requests or expense claims.
- `reimbursements`: Financial reimbursement records for finalized approved claims.
- `workflow_events`: Append-only immutable audit trail of state transitions, decisions, and system evaluations.

## Primary Keys & Conventions

| Entity | Primary Key | Format Example | Entity Type | Phase Introduced |
| :--- | :--- | :--- | :--- | :--- |
| `Employee` | `employee_id` | `EMP-000001` | Master | Phase 3 |
| `LeavePolicy` | `leave_policy_id` | `LPOL-000001` | Master | Phase 3 |
| `LeaveBalance` | `leave_balance_id` | `LBAL-000001` | Master | Phase 3 |
| `CostCenter` | `cost_center_id` | `CC-000001` | Master | Phase 3 |
| `ExpensePolicy` | `expense_policy_id` | `EPOL-000001` | Master | Phase 3 |
| `ApprovalRule` | `approval_rule_id` | `ARUL-000001` | Master | Phase 3 |
| `LeaveRequest` | `leave_request_id` | `LVE-2026-000001` | Transaction | Phase 4 |
| `ExpenseClaim` | `expense_claim_id` | `EXP-2026-000001` | Transaction | Phase 4 |
| `ExpenseLine` | `expense_line_id` | `EXPL-EXP-2026-000001-01` | Transaction | Phase 4 |
| `ApprovalStep` | `approval_step_id` | `APR-LVE-2026-000001-01` / `APR-EXP-2026-000001-01` | Workflow | Phase 5 / Phase 7 |
| `Reimbursement` | `reimbursement_id` | `RMB-2026-000001` | Workflow | Phase 5 / Phase 9 |
| `WorkflowEvent` | `event_id` | `EVT-2026-000001` / UUID | Audit | Phase 9 |

## Integrity Constraints & Relationships

- `employees.manager_id` → `employees.employee_id` (Self-referential; NULL only for CEO).
- `employees.cost_center_id` → `cost_centers.cost_center_id`.
- `leave_balances.employee_id` → `employees.employee_id`.
- `leave_balances.leave_policy_id` → `leave_policies.leave_policy_id`.
- `cost_centers.owner_employee_id` → `employees.employee_id`.
- `leave_requests.employee_id` → `employees.employee_id`.
- `leave_requests.leave_policy_id` → `leave_policies.leave_policy_id`.
- `expense_claims.employee_id` → `employees.employee_id`.
- `expense_claims.cost_center_id` → `cost_centers.cost_center_id`.
- `expense_claims.expense_policy_id` → `expense_policies.expense_policy_id`.
- `expense_lines.expense_claim_id` → `expense_claims.expense_claim_id`.
- `approval_steps.leave_request_id` → `leave_requests.leave_request_id` (optional).
- `approval_steps.expense_claim_id` → `expense_claims.expense_claim_id` (optional).
- `approval_steps.approval_rule_id` → `approval_rules.approval_rule_id` (optional).
- `approval_steps.approver_employee_id` → `employees.employee_id`.
- `reimbursements.expense_claim_id` → `expense_claims.expense_claim_id` (Unique constraint `uq_reimbursement_expense_claim` enforces strict 1:1 relationship).
- `workflow_events.actor_employee_id` → `employees.employee_id` (optional).

### Check Constraints & Business Rules
- `approval_steps`: Single parent constraint (exactly one of `leave_request_id` or `expense_claim_id` must be NOT NULL). Steps follow strictly monotonic order (`step_order` 1..N).
- `reimbursements`: Exactly one reimbursement per expense claim. Reimbursements can only be created when the expense claim is finally approved (`status == 'approved'`). Payment transitions are irreversible (`status == 'paid'`).
- `workflow_events`: Append-only, immutable history recording `REQUEST_SUBMITTED`, `ELIGIBILITY_EVALUATED`, `ROUTE_RESOLVED`, `STEP_CREATED`, `STEP_DECIDED`, `FINAL_DECISION`, `REIMBURSEMENT_CREATED`, and `REIMBURSEMENT_PAID`.

---

## 4. Presentation & Routing Layer (Phase 10 & 11)

### Model Properties & Helpers
Added non-intrusive presentation-layer properties to `models/__init__.py` that expose normalized business attributes without mutating underlying DB schemas:
- `Employee.role`: Dynamic organizational role derived from hierarchy (`Executive` for CEO without manager, `Manager` for employees with direct reports, `Employee` otherwise).
- `ApprovalStep.authority_level`: Resolves rule authority level via linked `ApprovalRule` or falls back to `step_order`.
- `LeavePolicy.category` / `annual_allowance` / `max_carryover_days` / `advance_notice_days` / `requires_approval` / `is_paid`: Exposes policy configurations cleanly.
- `ExpensePolicy.requires_approval` / `max_single_transaction` / `daily_limit`: Exposes transaction-level policy limits.
- `ApprovalRule.threshold_min` / `threshold_max` / `is_auto_approved`: Accessors for approval rule routing thresholds.
- `Reimbursement.payment_method`: Presentation default payment channel (`Direct Deposit (ACH / NEFT / BACS)`).
- `WorkflowEvent.timestamp` / `entity_type` / `details`: Aliases and parsers for event metadata.

### Flask Route Blueprints & Endpoints

| Blueprint | Route Prefix | Main Endpoints | Purpose |
| :--- | :--- | :--- | :--- |
| `main` | `/` | `/`, `/switch-user`, `/health` | Executive dashboard, persona switcher, production health check probe |
| `employee` | `/employee` | `/`, `/leave/new`, `/leave/<id>`, `/expense/new`, `/expense/<id>` | Employee self-service hub, leave/expense drafting & submission |
| `approvals`| `/approvals` | `/`, `/review/<step_id>`, `/decide` | Actionable manager/executive approval inbox and step decisioning |
| `finance` | `/finance` | `/`, `/reimbursements/<id>/pay`, `/budgets` | Reimbursement payment tracking, cost center budget health analysis |
| `admin` | `/admin` | `/`, `/employees`, `/policies`, `/rules`, `/cost-centers`, `/audit-log` | System governance directory, policy/rule matrix, audit event stream |
| `api` | `/api` | `/calculate-leave`, `/cost-center-budget/<id>`, `/policies-for-country/<c>` | Reactive JSON APIs for client-side forms and budget inspection |

