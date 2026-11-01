# SmartFlow – Leave and Expense Request Routing System

SmartFlow is an enterprise-grade leave and expense request routing and approval system built on Python, Flask, SQLite, and SQLAlchemy. It features multi-currency cost center budget validation, dynamic hierarchical approval workflows, deterministic anomaly detection, and automated reimbursement lifecycles.

---

## 1. Stack and Architecture

- **Backend**: Python 3.14, Flask, Flask-SQLAlchemy, SQLAlchemy 2.0
- **Database**: SQLite with `PRAGMA foreign_keys = ON`
- **Data Generation**: Pure Python standard libraries (`random`, `datetime`, `decimal`, `hashlib`) and `Faker`
- **Seed & Determinism**: Fixed seed `20261022` across all generators and workflows

```
                             [Client / Request Entry]
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │          Workflow Service             │
                     │    (services/workflow_service.py)     │
                     └───────────────────┬───────────────────┘
                                         │
                ┌────────────────────────┼────────────────────────┐
                ▼                        ▼                        ▼
     ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
     │    Leave Service     │ │   Expense Service    │ │   Approval Service   │
     │(Business days, tenure│ │(Category limits, CC  │ │(Tiered rules, manager│
     │ policies, balances)  │ │ budget, receipts)    │ │ hierarchy, step ord) │
     └──────────────────────┘ └──────────────────────┘ └──────────────────────┘
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │        Reimbursement Service          │
                     │ (services/reimbursement_service.py)   │
                     │  (Voucher creation, payment lifecycle)│
                     └───────────────────┬───────────────────┘
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │          Audit & Persistence          │
                     │  (SQLAlchemy models + WorkflowEvent)  │
                     └───────────────────────────────────────┘
```

---

## 2. Core Capabilities

### Cost Center Budget Validation
- **Single-Currency Principle**: Enforces canonical currency by country (`India` → `INR`, `USA` → `USD`, `UK` → `GBP`, `Canada` → `CAD`, `Australia` → `AUD`).
- **Approved Claims Aggregation**: Calculates used budget from finalized approved claims without double-counting reimbursements or pending claims.
- **Structured Decision Codes**: Accurately flags `BUDGET_EXCEEDED`, `BUDGET_EXHAUSTED`, `COUNTRY_COST_CENTER_MISMATCH`, and `CURRENCY_MISMATCH`.
- **Non-Mutating**: Read-only evaluation exposing budget used, remaining, and utilization percentage.

### End-to-End Workflow Orchestration
- **Eligibility Pre-Filtering**: Evaluates leave and expense eligibility before creating approval steps.
- **Dynamic Routing**: Traverses employee-manager hierarchies across tiered approval rules up to executive limits.
- **Strict Sequential Steps**: Enforces step order monotonicity (1..N) and blocks decisions on subsequent steps while prior steps remain pending.
- **Integrity Safeguards**: Prevents self-approval, unauthorized approvers, and duplicate step creation.

### Reimbursement Lifecycle
- **Strict Approval Gate**: Reimbursements can only be created for finalized approved claims (`status == 'approved'`).
- **Audit Trail & Deterministic IDs**: IDs generated as `RMB-YYYY-NNNNNN`. Enforces `uq_reimbursement_expense_claim` (1:1 claim-to-reimbursement relationship).
- **Payment Processing**: Validates monotonic timestamps (`paid_at >= created_at`) and prevents duplicate payments.

### Anomaly Injection & Detection Pipeline
- **14 Violation Categories + 4 Normal Controls**: Synthetically evaluates edge cases across policy limits, negative amounts, receipt tampering, and out-of-order approvals.
- **Isolated Fixture Evaluation**: Preserves baseline data integrity while benchmarking classification metrics (100% precision, 100% recall, 0 false positives).

---

## 3. Database Safety & Preservation

> [!IMPORTANT]
> - `instance/smartflow.db` and backup `instance/smartflow.db.bak`: Strictly read-only production baseline snapshots from Phases 1-8, preserved byte-for-byte across all runs.
> - `instance/phase9_smartflow.db`: Preserved Phase 9 baseline simulation database.
> - `instance/phase9_smartflow_v2.db`: Preserved Phase 9.1 dataset (13,649 events including historical audit event).
> - `instance/phase9_smartflow_submission.db`: Clean final submission database generated with seed `20261022` and workflow simulation enabled. Contains exactly 13,645 baseline events (0 test-generated events; 2,543 `STEP_DECIDED` events exactly matching the 2,543 decided approval steps). Set as default in `config.py`.
> - All automated test suites (`tests/test_access_control.py`, `tests/test_frontend.py`, `conftest.py`) execute against isolated temporary fixture clones, guaranteeing zero mutation to any submitted database file.

---

## 4. Final Submission Dataset (`instance/phase9_smartflow_submission.db`)

The final submission database is generated with seed `20261022` and workflow simulation enabled:

- **Country-Matched Leave Selection**: Normal baseline leave requests are restricted to policies matching the employee's country (`India` → Annual/Bereavement, `USA` → Sick, `UK` → Casual, `Canada` → Maternity, `Australia` → Paternity). Artificial `COUNTRY_POLICY_MISMATCH` records are eliminated from baseline generation (0 occurrences) while retained as deliberate anomalies in test/validation suites.
- **Approval Hierarchy Exceptions**: Employees reporting to inactive manager `EMP-000047` and the CEO `EMP-000001` (who has no manager) legitimately fail routing with explicit code `INACTIVE_OR_MISSING_APPROVER` (18 occurrences), preserving authorization boundaries without permitting self-approval or unauthorized approval.
- **Available Balance Enforcement**: Multi-request reference year tracking accurately detects 44 requests exceeding available balance (`BALANCE_EXCEEDED`).
- **Submission Dataset Metrics**:
  - **Employees**: 400 total
  - **Cost Centers**: 20 total
  - **Leave Policies**: 6 total
  - **Leave Balances**: 1,965 total
  - **Expense Policies**: 35 total
  - **Approval Rules**: 155 total
  - **Leave Requests**: 800 total | 738 eligible (92.25%) | 62 ineligible (7.75%: 44 balance exceeded, 18 inactive approver).
  - **Expense Claims**: 1,000 total | 982 eligible (98.20%) | 18 ineligible (1.80%).
  - **Expense Lines**: 5,500 total.
  - **Approval Steps**: 2,965 total | 2,353 approved, 190 rejected, 422 pending.
  - **Reimbursements**: 656 total | 524 paid, 132 pending; \$1,108,341.00 total reimbursed, \$877,614.10 paid.
  - **Workflow Events**: Exactly 13,645 auditable transition events logged (0 test-generated events; 2,543 `STEP_DECIDED` events matching 2,543 decided steps).
- **Two-Run Reproducibility**: 100.000% reproducible across independent generation runs with matching 64-character SHA-256 table checksums for all 12 entities.

---

## 5. Phase 10 – Frontend Implementation

SmartFlow features a fully integrated, responsive enterprise web frontend powered by Flask and Jinja2 templates, styled with a modern, self-contained CSS design system (`static/css/style.css`) and responsive client interactions (`static/js/app.js`):

### 1. Unified Operational Dashboard (`/`)
- Real-time KPIs: 400 employees, 20 cost centers, 800 leave requests, 1,000 claims, 2,965 approval steps, and 656 reimbursements.
- Cost Center Budget Health widget with interactive progress bars tracking allocated vs. utilized annual budgets.
- Pending Approvals Inbox preview with direct review links.
- **Demo Persona Switcher**: Session-based persona switcher in the top navigation allowing instant switching between Employee, Regional Manager, Finance, and Executive/Admin perspectives for evaluation and testing. (Clearly identified as simulation-only; not production SSO).

### 2. Employee Self-Service Portal (`/employee/`)
- Real-time leave balance overview showing annual allowance, used days, and available days.
- **Leave Request Submission (`/employee/leave/new`)**: Reactive working day calculator excluding weekends and country-specific holidays.
- **Multi-Line Expense Claim Submission (`/employee/expense/new`)**: Dynamic line item adding/removing, automatic receipt attachment detection, and real-time total amount computation.
- **Visual Workflow Steppers (`/employee/leave/<id>`, `/employee/expense/<id>`)**: Step-by-step progress tracking with approver designations, timestamps, and decision history.

### 3. Manager & Approver Portal (`/approvals/`)
- Actionable inbox displaying pending approval steps assigned strictly to the active manager.
- Granular review interface (`/approvals/review/<step_id>`) detailing employee tenure, policy limits, cost center budget status, and prior approval history.
- Authorized decision processing (`/approvals/decide`) enforcing monotonic step order (1..N) and blocking decisions when prior steps are pending.

### 4. Finance & Reimbursement Portal (`/finance/`)
- Comprehensive voucher tracking with status filters (`All`, `Pending`, `Paid`).
- Direct payment disbursement action (`POST /finance/reimbursements/<id>/pay`) updating status to `PAID` with verified monotonic timestamps (`paid_at >= created_at`). Guarded by route-level authorization restricting payment processing to Finance personnel and Executives.
- Cost center budget health directory (`/finance/budgets`) ranking departmental spend across all 20 cost centers.

### 5. Admin & Governance Directory (`/admin/`)
- Enterprise telemetry overview displaying system counts and operational health.
- Paginated Employee Directory (`/admin/employees`) with search by name, employee ID, and country.
- Master Policy Matrix (`/admin/policies`) covering 6 statutory leave policies and 35 categorical expense policies.
- Tiered Approval Rules Matrix (`/admin/rules`) displaying all 155 approval thresholds and authority levels.
- Departmental Cost Centers (`/admin/cost-centers`) detailing currency-isolated annual budgets and cost center owners.
- Immutable System Audit Stream (`/admin/audit-log`) indexing all 13,649 workflow events with search and filtering.
- Route-level access control hook (`@admin_bp.before_request`) restricting directory access to Executive leadership and Operations personnel.

### 6. Reactive JSON APIs (`/api/`)
- `/api/calculate-leave`: Instant business days calculation excluding holidays and weekends.
- `/api/cost-center-budget/<id>`: Cost center utilization and remaining budget metrics.
- `/api/policies-for-country/<country>`: Country-specific policy options for client dropdowns.

---

## 6. Phase 11 – Final Integration, Access Control & Production Readiness

- **Production Health Probe (`/health`)**: Enterprise WSGI health endpoint reporting application status, database connectivity, master record counts, and transaction telemetry.
- **Route-Level Authorization & Access Control**:
  - `POST /finance/reimbursements/<id>/pay`: Enforces that only personas in the `Finance` department or with the `Executive` role can disburse payments. Unauthorized users (e.g. Staff Employees in HR or Sales) are blocked and redirected with an error alert.
  - `/admin/*`: Enforces that only users with the `Executive` role (e.g. CEO `EMP-000001`) or in the `Operations` department (e.g. VP Operations `EMP-000006`) can access system configuration and audit logs. All others are denied access.
- **Simulated Authentication Disclosure (Demo-Only)**:
  - SmartFlow's `/switch-user` feature is a **simulated demo persona switcher** designed specifically for project evaluation, grading, and testing multi-role workflows.
  - It does **not** provide production-grade authentication (no passwords, salt hashing, MFA, JWT tokens, or enterprise SSO via SAML/OAuth2/OIDC).
  - Production deployment would require replacing the demo switcher with an enterprise Identity Provider (IdP) integration, encrypted session cookies, CSRF tokens on all state-changing endpoints, and API rate limiting.
- **Strict Approval Integrity**: Enforces single-parent constraints, eliminates self-approval, enforces sequential step progression, and gates reimbursement generation strictly behind final approval.
- **Currency Isolation**: Strict country-to-currency binding prevents multi-currency budget calculation anomalies.
- **Isolated Automated Test Suite**: Comprehensive automated test suites (`tests/test_access_control.py` and `tests/test_frontend.py`) dynamically rebind SQLite engines to temporary database clones during test execution, guaranteeing zero modification to submitted database files.

## 7. Setup and Quickstart

### Prerequisites
- Python 3.10+ (tested on Python 3.14)

### Installation
```bash
python -m venv venv
# Windows (PowerShell):
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### Running the Web Application
```bash
# Start the SmartFlow web server
python app.py
```
Open your browser and navigate to:
```
http://127.0.0.1:5000/
```

### Running the Validation Pipeline
```bash
# Execute validation pipeline against clean submission dataset
# PowerShell:
$env:DATABASE_URL = "sqlite:///C:/Users/Karthik/Desktop/SmartFlow/instance/phase9_smartflow_submission.db"
python -m validation.validate

# Linux / macOS (bash):
export DATABASE_URL="sqlite:///$(pwd)/instance/phase9_smartflow_submission.db"
python -m validation.validate
```

### Running Tests
```bash
# Run isolated access control suite (10 tests)
python -m pytest tests/test_access_control.py -v

# Run frontend portal test suite (30 tests)
python -m pytest tests/test_frontend.py -v

# Run full regression test suite across all 11 phases (147 tests)
python -m pytest -v
```

---

## 8. Verification & Validation Metrics

Running `python -m validation.validate` against `instance/phase9_smartflow_v2.db` produces:
- `reports/validation_report.json`
- `reports/validation_summary.md`

### Verification Highlights:
- **Accuracy**: 100.00%
- **Precision**: 100.00%
- **Recall**: 100.00%
- **Specificity**: 100.00%
- **F1 Score**: 1.0000
- **Workflow Lifecycle Integrity**: 12 checks passed (0 sequence issues, 0 self-approvals, 0 unapproved reimbursements, 0 budget arithmetic mismatches).
