# Source Manifest

## Data source

Master and transaction data are synthetic. Generated using pure Python (`random`, `datetime`, `decimal`, `hashlib`) and `Faker`. No external HR files, payroll extracts, or personal data are imported.

## Seed and reproducibility

- Fixed seed: `20261022`
- `random.seed(20261022)`
- `Faker.seed_instance(20261022)` for default faker and country locales
- The same seed and scale deterministically produce identical IDs, emails, leave requests, expense claims, lines, and receipt hashes.

## Generation methodology

### Master Data (Phase 3)
1. Create 20 cost centers with `owner_employee_id` empty.
2. Create employees (40 starter / 400 full) with Faker names, unique emails, valid cost centers, and a top-down manager tree.
3. Assign each cost center an owner who is a valid employee in the same country.
4. Create exactly 6 leave policies from country-specific templates.
5. Create leave balances only for employees employed during 2026. Accrual is months employed in 2026 / 12 × policy allowance. Remaining days are calculated, not sampled.
6. Create country-specific expense policies for seven categories (35 total).
7. Create non-overlapping leave and expense approval bands by country (155 total).

### Transaction Data (Phase 4)
1. **Leave Requests**:
   - 800 in full mode, 80 in starter mode.
   - IDs: `LVE-2026-000001` upwards.
   - Exact business-day calculations excluding weekends and 2026 public holidays for India, USA, UK, Canada, and Australia.
   - Start and end dates fall strictly within employee tenure in 2026.
   - No overlapping approved leaves for any individual employee.
   - Cumulative approved requests respect available leave balances.
2. **Expense Claims & Lines**:
   - 1,000 claims in full mode, 100 in starter mode.
   - IDs: `EXP-2026-000001` upwards.
   - Lines per claim: 1 to 10 (IDs: `EXPL-EXP-2026-000001-01` upwards).
   - Country matches employee country; policy matches country and category.
   - Claim total amount strictly equals `SUM(ExpenseLine.amount)`.
   - Claim total is within policy `max_amount`.
   - Unique synthetic receipt filename and SHA-256 hash metadata generated whenever line amount >= `receipt_required_above`.

### Leave Business Logic Service (Phase 5)
- Module: `services/leave_service.py`
- Implements:
  - Working business-day calculation excluding weekends and 2026 country public holidays.
  - Available leave balance calculation accounting for approved leave requests without double-counting `used_days`.
  - Non-mutating leave request evaluation checking employee tenure, policy validity, country matching, consecutive limits, overlap, and balances.
  - Structured decision generation with programmatic reason codes (`APPROVE` / `REJECT` / `NEEDS_REVIEW`).
- Unit test suite: `tests/test_leave_service.py` covering all 9 required business cases.

### Expense Business Logic Service (Phase 6)
- Module: `services/expense_service.py`
- Implements:
  - Receipt metadata extraction from `ExpenseLine.description` (`[Receipt: <filename> | SHA256: <hash>]`).
  - Validation of employee tenure, policy country and category match, cost center validity, positive amounts, and line count (1..10).
  - Strict total reconciliation: `ExpenseClaim.total_amount == SUM(ExpenseLine.amount)`.
  - Policy limit enforcement: `claim.total_amount <= policy.max_amount`.
  - Mandatory receipt presence checking for line amount $\ge$ `receipt_required_above`.
  - Cross-claim duplicate receipt detection.
  - Safe, non-mutating evaluation returning structured decisions (`APPROVE` / `REJECT` / `NEEDS_REVIEW`).
- Unit test suite: `tests/test_expense_service.py` covering all 14 required business cases.

### Approval Routing Service (Phase 7)
- Module: `services/approval_service.py`
- Implements:
  - Route resolution across tiered approval bands (`ApprovalRule`).
  - Management hierarchy traversal (direct manager to executive levels).
  - Step creation, sequential enforcement, self-approval prevention.
- Unit test suite: `tests/test_approval_service.py` (15 tests).

### Anomaly Injection & Detection Framework (Phase 8)
- Modules: `generator/anomaly_injector.py`, `validation/validate.py`
- Implements:
  - 14 business-rule violation categories and 4 normal controls.
  - Deterministic ground-truth manifest (`ground_truth/anomaly_manifest.json`).
  - Isolated fixture evaluation preventing corruption of baseline data.
- Unit test suites: `tests/test_anomaly_injector.py` (3 tests), `tests/test_validation_pipeline.py` (13 tests).

### Budget Validation, Workflow Orchestration & Reimbursements (Phase 9)
- Modules:
  - `services/expense_service.py`: Cost center budget utilization, currency consistency, and overrun detection.
  - `services/workflow_service.py`: End-to-end request submission, eligibility filtering, routing, and sequential step decisions.
  - `services/reimbursement_service.py`: Approved claim reimbursement creation, duplicate prevention, payment status lifecycle.
  - `models/__init__.py`: `WorkflowEvent` append-only audit model and `Reimbursement` unique constraint.
- Unit test suites:
  - `tests/test_budget_validation.py` (9 tests)
  - `tests/test_workflow_service.py` (12 tests)
  - `tests/test_reimbursement_service.py` (13 tests)

## Commands

```bash
# Generate starter dataset
python -m generator.generate_data --scale starter

# Generate full dataset
python -m generator.generate_data --scale full

# Run Phase 9 workflow lifecycle simulation
python -m generator.generate_data --scale full --simulate-workflow --target-db instance/phase9_smartflow.db

# Run Phase 9 validation pipeline and generate reports
python -m validation.validate

# Run all 97 unit tests
python -m unittest discover -s tests -p "test_*.py"
```

## Inventory after Phase 9

### Baseline Database Snapshot (`instance/smartflow.db`)
Preserved read-only snapshot containing Phases 1-8 master and transaction data:
- `cost_centers`: 20 rows
- `employees`: 400 rows
- `leave_policies`: 6 rows
- `leave_balances`: 1,965 rows
- `expense_policies`: 35 rows
- `approval_rules`: 155 rows
- `leave_requests`: 800 rows
- `expense_claims`: 1,000 rows
- `expense_lines`: 5,500 rows
- `approval_steps`: 0 rows
- `reimbursements`: 0 rows

### Phase 9 Simulated Workflow Database (`instance/phase9_smartflow.db`)
Full end-to-end workflow simulation generated with seed `20261022`:
- Master & Baseline Transactions: Identical to baseline snapshot.
- `approval_steps`: 2,209 rows (1,756 approved, 125 rejected, 328 pending)
- `reimbursements`: 657 rows (523 paid, 134 pending)
- `workflow_events`: 11,695 rows (comprehensive state transition audit log)

### Phase 9.1 Preserved Workflow Database (`instance/phase9_smartflow_v2.db`)
Preserved workflow dataset from Phase 9.1:
- Master Data: Identical to baseline snapshot (400 employees, 20 cost centers, 6 leave policies, 1,965 leave balances, 35 expense policies, 155 approval rules).
- `leave_requests`: 800 rows (738 eligible [92.25%], 62 ineligible [7.75%]; 599 approved, 138 rejected, 63 pending; 0 artificial country policy mismatches).
- `expense_claims`: 1,000 rows (982 eligible, 18 ineligible; 771 approved, 132 rejected, 97 pending).
- `expense_lines`: 5,500 rows.
- `approval_steps`: 2,965 rows (2,353 approved, 190 rejected, 422 pending).
- `reimbursements`: 656 rows (524 paid, 132 pending; $1,108,341.00 reimbursed, $877,614.10 paid).
- `workflow_events`: 13,649 rows (contains historical verification event from integration testing).
- Preserved without modification.

### Final Clean Submission Database (`instance/phase9_smartflow_submission.db`)
Default active production database configured in `config.py`. Clean full-scale generation with seed `20261022` and workflow simulation enabled:
- Master Data: Identical to baseline snapshot (400 employees, 20 cost centers, 6 leave policies, 1,965 leave balances, 35 expense policies, 155 approval rules).
- `leave_requests`: 800 rows (738 eligible [92.25%], 62 ineligible [7.75%]; 599 approved, 138 rejected, 63 pending; 0 artificial country policy mismatches).
- `expense_claims`: 1,000 rows (982 eligible, 18 ineligible; 771 approved, 132 rejected, 97 pending).
- `expense_lines`: 5,500 rows.
- `approval_steps`: 2,965 rows (2,353 approved, 190 rejected, 422 pending).
- `reimbursements`: 656 rows (524 paid, 132 pending; $1,108,341.00 reimbursed, $877,614.10 paid).
- `workflow_events`: Exactly 13,645 clean generated events (0 test-generated events; 2,543 `STEP_DECIDED` events exactly matching the 2,543 decided approval steps [2,353 approved + 190 rejected]).
- SHA-256 Hash: `0D9B9CB67FA962BEED851343108524E1371C0A06907AB29B3B33B13228FB4800`
- SQLite Integrity & Foreign Keys: 100% passed (`ok`, 0 FK violations).

### Phase 10 Frontend Implementation & Portals
- Blueprint Modules:
  - `routes/main.py`: Operational dashboard with KPIs, cost center budget utilization progress, persona switching, and `/health` probe.
  - `routes/employee.py`: Employee self-service hub, leave request submission, expense claim submission with dynamic line items, and request detail views with visual workflow approval steppers.
  - `routes/approvals.py`: Approver inbox filtered by active session persona, detailed request inspection, and authorized approve/reject decisions with sequential order enforcement.
  - `routes/finance.py`: Reimbursement queue with voucher tracking, disbursement action (`/finance/reimbursements/<id>/pay`), and cost center budget health metrics.
  - `routes/admin.py`: Enterprise directory (400 employees with pagination & search), policy matrix (leave & expense policies), approval rules (155 tiered bands), cost center budgets, and system-wide audit event log (13,649 events).
  - `routes/api.py`: Reactive JSON endpoints for client-side business day calculation, country-filtered policies, and real-time cost center budget status.
- Template Hierarchy (19 Jinja2 templates under `templates/`):
  - `base.html`, `dashboard.html`, `404.html`, `500.html`
  - `employee/hub.html`, `employee/leave_new.html`, `employee/expense_new.html`, `employee/leave_detail.html`, `employee/expense_detail.html`
  - `approvals/inbox.html`, `approvals/review.html`
  - `finance/portal.html`, `finance/budgets.html`
  - `admin/portal.html`, `admin/employees.html`, `admin/policies.html`, `admin/rules.html`, `admin/cost_centers.html`, `admin/audit_log.html`
- Static Assets:
  - `static/css/style.css`: Clean, responsive enterprise design system (CSS variables, flex/grid layouts, metric cards, status badges, workflow progress steppers, progress bars).
  - `static/js/app.js`: Real-time client-side business days calculator and dynamic multi-line expense line builder with automatic total calculation.
- Unit Test Suite:
  - `tests/test_frontend.py`: 30 automated integration tests covering all portals, form submissions, security boundaries, and API endpoints.

### Phase 11 Production Readiness & Final Verification
- Health & Monitoring: `/health` probe reporting application state, database connectivity, and entity metrics.
- Route-Level Authorization & Access Controls:
  - Payment action `POST /finance/reimbursements/<id>/pay` restricted to authorized Finance and Executive personas.
  - Admin operations `/admin/*` guarded by `@admin_bp.before_request` restricted to Executive and Operations personnel.
  - Isolated access control test suite `tests/test_access_control.py` (10 tests) verifying authorization gates using temporary cloned test fixtures.
- Simulated Authentication Disclosure: Disclosed that `/switch-user` provides demo persona switching for evaluation and testing, not production-grade SSO/IdP authentication.
- Architecture Documentation: `docs/architecture.md` (Sections 10 and 11) and updated `docs/schema.md`.
- End-to-end regression coverage across 147 tests.



