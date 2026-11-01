# Architecture

SmartFlow Leave and Expense Request Routing System architecture and service flow.

## 1. System Overview

SmartFlow is built on Python, Flask, and Flask-SQLAlchemy with an SQLite database (configured by default to `instance/phase9_smartflow_submission.db` in `config.py`). The architecture separates:
- **Models (`models/`)**: SQLAlchemy entity schemas and SQLite foreign key listeners (`PRAGMA foreign_keys = ON`).
- **Data Generator (`generator/`)**: Deterministic synthetic generation for master (Phase 3) and transaction (Phase 4) records using fixed seed `20261022`.
- **Services (`services/`)**: Pure business logic services that evaluate requests, check policies, compute balances, and enforce compliance without mutating database state unnecessarily.
- **Validation & Tests (`tests/`, `validation/`)**: Automated test suites verifying business rules and constraints.
- **Presentation Layer (`routes/`, `templates/`, `static/`)**: Multi-portal user interface delivering responsive web portals for employees, managers, finance, and administrators.

## 2. Leave Service Architecture (`services/leave_service.py`)

Phase 5 introduces the Leave Business Logic service. It is modular, beginner-friendly, and completely decoupled from database mutations.

### Evaluation Flow

```
Leave Request (Object / Dict / ID)
               │
               ▼
   1. Employee Existence Check ──── (Not Found) ─────► REJECT (EMPLOYEE_NOT_FOUND)
               │
               ▼
   2. Leave Policy Validity Check ── (Not Found) ────► REJECT (POLICY_NOT_FOUND)
               │
               ▼
   3. Country-Policy Match Check ── (Mismatch) ──────► REJECT (COUNTRY_POLICY_MISMATCH)
               │
               ▼
   4. Date Ordering Check ───────── (Start > End) ───► REJECT (INVALID_DATE_RANGE)
               │
               ▼
   5. Employment Tenure Check ───── (Outside) ───────► REJECT (OUTSIDE_EMPLOYMENT_PERIOD)
               │
               ▼
   6. Business Days Calculation ─── (0 Working Days) ► REJECT (INVALID_DATE_RANGE)
               │
               ▼
   7. Policy Consecutive Limit ──── (Days > Limit) ──► REJECT (POLICY_LIMIT_EXCEEDED)
               │
               ▼
   8. Approved Overlap Check ────── (Conflict) ──────► REJECT (OVERLAPPING_APPROVED_LEAVE)
               │
               ▼
   9. Available Balance Check ───── (<= 0 Days) ─────► REJECT (NO_AVAILABLE_BALANCE)
               │                   (Req > Avail) ────► REJECT (BALANCE_EXCEEDED)
               ▼
   10. Eligibility Passed:
       - If status == 'approved' or auto_approve: APPROVE (ELIGIBLE_FOR_APPROVAL)
       - If pending manager review (default):    NEEDS_REVIEW (ELIGIBLE_FOR_APPROVAL)
```

### Leave Balance Calculation & Double-Counting Prevention

1. **Master Snapshot**:
   The `leave_balances` table stores master baseline records:
   $$\text{remaining\_days} = \text{opening\_balance} + \text{accrued\_days} - \text{used\_days}$$
2. **Double-Counting Avoidance**:
   `used_days` is already deducted from the total allowance when computing `remaining_days`. Therefore, `used_days` is **never deducted a second time**.
3. **Approved Requests Accounting**:
   When evaluating an employee's available balance for a policy in year $Y$:
   $$\text{available\_days} = \max\left(0.00, \text{remaining\_days} - \sum_{\substack{r \in \text{ApprovedRequests} \\ r \ne \text{current\_request}}} r.\text{business\_days}\right)$$
   If re-evaluating an existing approved request, that request is excluded from the deduction to avoid self-deduction.

### Business-Day & Holiday Calendar Logic

Working days are calculated strictly between `start_date` and `end_date` (inclusive):
- Saturdays (`weekday == 5`) and Sundays (`weekday == 6`) are excluded.
- Documented 2026 statutory public holidays for the employee's country (`India`, `USA`, `UK`, `Canada`, `Australia`) are excluded.
- Spans with zero working days are rejected with `INVALID_DATE_RANGE`.

### Decision Matrix & Reason Codes

| Decision | Reason Code | Trigger Condition |
| :--- | :--- | :--- |
| `REJECT` | `EMPLOYEE_NOT_FOUND` | `employee_id` does not exist in `employees`. |
| `REJECT` | `POLICY_NOT_FOUND` | `leave_policy_id` does not exist or `is_active == False`. |
| `REJECT` | `COUNTRY_POLICY_MISMATCH` | `policy.country != employee.country` when country match is enforced. |
| `REJECT` | `INVALID_DATE_RANGE` | `start_date > end_date` or span has 0 business days. |
| `REJECT` | `OUTSIDE_EMPLOYMENT_PERIOD` | Dates before `hire_date` or after `termination_date`. |
| `REJECT` | `POLICY_LIMIT_EXCEEDED` | `requested_days > policy.max_consecutive_days`. |
| `REJECT` | `OVERLAPPING_APPROVED_LEAVE` | Dates overlap with an already approved leave for that employee. |
| `REJECT` | `NO_AVAILABLE_BALANCE` | Employee has no balance record or `available_days <= 0`. |
| `REJECT` | `BALANCE_EXCEEDED` | `requested_days > available_days`. |
| `APPROVE` | `ELIGIBLE_FOR_APPROVAL` | Request meets all criteria and is already approved or auto-approvable. |
| `NEEDS_REVIEW` | `ELIGIBLE_FOR_APPROVAL` | Request meets all criteria and is pending manager/authority review. |

### Immutability & Repeatability

The `evaluate_leave_request()` function is purely read-only and idempotent:
- Does not create `ApprovalStep` records.
- Does not modify `LeaveRequest.status`.
- Does not modify `LeaveBalance` records.
- Repeated executions on the same input return identical decisions.

## 3. Expense Service Architecture (`services/expense_service.py`)

Phase 6 introduces the Expense Business Logic service. It provides robust validation of expense claims, line items, policy category matching, limits, line total reconciliation, and receipt requirements without modifying database records.

### Evaluation Flow

```
Expense Claim (Object / Dict / ID)
               │
               ▼
   1. Employee Existence Check ──── (Not Found) ─────► REJECT (EMPLOYEE_NOT_FOUND)
               │
               ▼
   2. Policy Validity & Active ──── (Not Found) ─────► REJECT (POLICY_NOT_FOUND)
               │
               ▼
   3. Country-Policy Match Check ── (Mismatch) ──────► REJECT (COUNTRY_POLICY_MISMATCH)
               │
               ▼
   4. Cost Center Validity Check ── (Invalid/Missing) ► REJECT (COST_CENTER_NOT_FOUND)
               │
               ▼
   5. Line Count Check (1..10) ──── (Count <1 or >10) ► REJECT (INVALID_LINE_COUNT)
               │
               ▼
   6. Claim Date vs Tenure ──────── (Outside Tenure) ─► REJECT (EXPENSE_DATE_OUTSIDE_EMPLOYMENT)
               │
               ▼
   7. Line-by-Line Iteration:
      - Line Category Match ─────── (Mismatch) ──────► REJECT (CATEGORY_POLICY_MISMATCH)
      - Line Amount Positive ────── (<= 0) ──────────► REJECT (INVALID_EXPENSE_AMOUNT)
      - Line Date <= Claim Date ─── (Date > Claim) ──► REJECT (INVALID_EXPENSE_DATE)
      - Line Date vs Tenure ─────── (Outside Tenure) ─► REJECT (EXPENSE_DATE_OUTSIDE_EMPLOYMENT)
      - Receipt Threshold Check ─── (Missing Rec) ───► REJECT (RECEIPT_REQUIRED)
      - Duplicate Hash Check ────── (Dup Detected) ──► REJECT (DUPLICATE_RECEIPT)
               │
               ▼
   8. Claim Total Reconciliation ── (Claim != Lines) ─► REJECT (CLAIM_TOTAL_MISMATCH)
               │
               ▼
   9. Policy Limit Check ────────── (Total > Limit) ──► REJECT (POLICY_LIMIT_EXCEEDED)
               │
               ▼
   10. Eligibility Passed:
       - If status == 'approved' or auto_approve: APPROVE (ELIGIBLE_FOR_APPROVAL)
       - If pending manager review (default):    NEEDS_REVIEW (ELIGIBLE_FOR_APPROVAL)
```

### Receipt Metadata Handling

- **Database Limitation**: `ExpenseLine` has no dedicated `receipt_filename` or `receipt_hash` columns.
- **Solution**: Receipt metadata is embedded in `ExpenseLine.description` using the format `[Receipt: <filename> | SHA256: <hash>]`.
- **Extraction**: The service safely extracts this metadata using regex without requiring schema alteration.
- **Cross-Claim Duplicate Detection**: If an expense line carries a receipt hash, the service queries existing lines in other claims (`ExpenseLine.description.like(f"%{hash}%")`) to prevent duplicate receipt submissions.

### Decision Matrix & Reason Codes

| Decision | Reason Code | Trigger Condition |
| :--- | :--- | :--- |
| `REJECT` | `EMPLOYEE_NOT_FOUND` | `employee_id` does not exist in `employees`. |
| `REJECT` | `POLICY_NOT_FOUND` | `expense_policy_id` does not exist or `is_active == False`. |
| `REJECT` | `INVALID_EMPLOYMENT_DATE` | Employee has missing or invalid hire date. |
| `REJECT` | `COUNTRY_POLICY_MISMATCH` | `policy.country != employee.country` or `policy.country != claim.country`. |
| `REJECT` | `COST_CENTER_NOT_FOUND` | `cost_center_id` does not exist or `is_active == False`. |
| `REJECT` | `INVALID_LINE_COUNT` | Line count is less than 1 or greater than 10. |
| `REJECT` | `EXPENSE_DATE_OUTSIDE_EMPLOYMENT` | Claim date or line date is before `hire_date` or after `termination_date`. |
| `REJECT` | `CATEGORY_POLICY_MISMATCH` | Line category does not match `policy.category`. |
| `REJECT` | `INVALID_EXPENSE_AMOUNT` | Line amount is $\le 0$. |
| `REJECT` | `INVALID_EXPENSE_DATE` | Line date is missing or after `claim_date`. |
| `REJECT` | `RECEIPT_REQUIRED` | Line amount $\ge$ `receipt_required_above` but missing receipt metadata. |
| `REJECT` | `DUPLICATE_RECEIPT` | Receipt hash is duplicated within the claim or in another claim. |
| `REJECT` | `CLAIM_TOTAL_MISMATCH` | `claim.total_amount != SUM(line.amount)`. |
| `REJECT` | `POLICY_LIMIT_EXCEEDED` | `claim.total_amount > policy.max_amount`. |
| `APPROVE` | `ELIGIBLE_FOR_APPROVAL` | Claim meets all criteria and is already approved or auto-approvable. |
| `NEEDS_REVIEW` | `ELIGIBLE_FOR_APPROVAL` | Claim meets all criteria and is pending manager/authority review. |

## 4. Cost Center Budget Validation Architecture (`services/expense_service.py`) — Phase 9

Phase 9 integrates budget-aware validation into the expense service, enforcing fiscal responsibility at the cost center level.

### Budget Utilization Mechanics
1. **Single-Currency Principle**:
   - Each country has a canonical currency defined in `COUNTRY_CURRENCIES` (e.g., `India` → `INR`, `USA` → `USD`).
   - Budget calculations strictly forbid cross-currency aggregation without an explicit conversion model.
   - Any currency or country mismatch between employee, claim, and cost center immediately triggers rejection.
2. **Double-Counting Avoidance**:
   - Budget utilization is calculated strictly from finalized approved expense claims (`status == 'approved'`).
   - Pending claims and reimbursed payments are not aggregated separately, ensuring clean single-source accounting.
   - When evaluating a claim that is already approved (e.g. during re-evaluation), the claim itself is excluded via `exclude_claim_id` to avoid self-deduction.
3. **Budget Formulas**:
   $$\text{budget\_used} = \sum_{\substack{c \in \text{ApprovedClaims} \\ c.\text{cost\_center\_id} = CC \\ c.\text{claim\_date}.\text{year} = Y}} c.\text{total\_amount}$$
   $$\text{budget\_remaining} = \max(0.00, \text{annual\_budget} - \text{budget\_used})$$
   $$\text{utilization\_pct} = \frac{\text{budget\_used}}{\text{annual\_budget}} \times 100$$
4. **Structured Decision Codes**:
   - `BUDGET_EXCEEDED`: `requested_amount > budget_remaining`.
   - `BUDGET_EXHAUSTED`: `budget_remaining <= 0.00`.
   - `COUNTRY_COST_CENTER_MISMATCH`: Cost center country does not match claim or employee country.
   - `CURRENCY_MISMATCH`: Claim currency does not match cost center country currency.

## 5. Approval Routing Service Architecture (`services/approval_service.py`) — Phase 7

Provides authority-level routing resolution and sequential step execution:
- Evaluates request thresholds against tiered rules (`ApprovalRule`).
- Resolves sequential approver hierarchy (Manager → Director → VP → CFO/CEO).
- Enforces strict sequential processing (`step_order` 1..N).
- Prevents self-approval (`approver_employee_id != request.employee_id`).
- Detects inactive or missing approvers (`INACTIVE_OR_MISSING_APPROVER`).

## 6. Workflow Orchestration Service Architecture (`services/workflow_service.py`) — Phase 9

Phase 9 introduces the central orchestration service coordinating the end-to-end lifecycle.

```
Request Submission (LeaveRequest / ExpenseClaim)
                     │
                     ▼
       1. Eligibility Evaluation
     (leave_service / expense_service)
          │                  │
      [Ineligible]       [Eligible]
          │                  │
          ▼                  ▼
    Mark REJECTED       2. Route Resolution
    Log Audit Event    (approval_service.resolve_approval_route)
                             │
                             ▼
                     3. Approval Step Creation
                        (Deterministic IDs)
                             │
                             ▼
                     4. Approver Review Loop
                        (Step-by-Step Monotonic)
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
            [Step Approved]         [Step Rejected]
                 │                       │
           Is Final Step?          Mark Request REJECTED
           ┌─────┴─────┐           Halt Downstream Steps
          No          Yes          Log Audit Event
           │           │
       Advance     Mark Request APPROVED
       to Next     Trigger Reimbursement (if Expense)
       Step        Log Audit Event
```

### Key Principles
- **Idempotency**: Duplicate routing calls return existing workflow state without creating redundant steps.
- **Fail-Fast**: Ineligible requests are rejected immediately without creating dangling approval steps.
- **Atomic Transitions**: All state changes and audit events commit within single database transactions.

## 7. Reimbursement Service Architecture (`services/reimbursement_service.py`) — Phase 9

Manages the financial payment lifecycle for approved expense claims.

### Lifecycle Rules
1. **Approval Prerequisite**: A reimbursement may only be created when `ExpenseClaim.status == 'approved'`.
2. **Step Verification**: All associated `ApprovalStep` records must be marked `approved`.
3. **Exact Amount Match**: `Reimbursement.amount == ExpenseClaim.total_amount`.
4. **Deterministic Identifier**: Generated with format `RMB-YYYY-NNNNNN`.
5. **One-to-One Protection**: Database unique constraint `uq_reimbursement_expense_claim` prevents duplicate claims.
6. **Chronology Enforcement**: `paid_at` must be on or after `created_at`.
7. **Idempotent Payment**: Once marked `paid`, a reimbursement cannot be paid again.

## 8. Event History and Auditability (`models.WorkflowEvent`) — Phase 9

Every state transition is persisted in an append-only audit model:
- `REQUEST_SUBMITTED`: Request entered system.
- `ELIGIBILITY_EVALUATED`: Leave/expense service decision and reason code.
- `ROUTE_RESOLVED`: Tiered approval rules and approver sequence.
- `STEP_CREATED`: Sequential approval step persisted.
- `STEP_DECIDED`: Approver action recorded.
- `FINAL_DECISION`: Request reached terminal state (`approved` or `rejected`).
- `REIMBURSEMENT_CREATED`: Payment voucher generated for claim.
- `REIMBURSEMENT_PAID`: Voucher settled with disbursement timestamp.

## 9. Phase 9.1 – Leave Data Quality Correction & Two-Run Reproducibility

Phase 9.1 addresses leave policy assignment realism and formalizes two-run reproducibility:

1. **Country-Matched Leave Policy Assignment**:
   - Generator restricts baseline leave requests to policies matching the employee's country (`policy.country == employee.country`).
   - India employees access Annual (`LPOL-000001`) and Bereavement (`LPOL-000006`) leave.
   - USA employees access Sick (`LPOL-000002`) leave.
   - UK employees access Casual (`LPOL-000003`) leave.
   - Canada employees access Maternity (`LPOL-000004`) leave.
   - Australia employees access Paternity (`LPOL-000005`) leave.
   - Eliminates artificial baseline mismatches (0 normal policy mismatches).

2. **Approval Hierarchy Exceptions**:
   - Submissions by employees reporting to inactive manager `EMP-000047` and CEO `EMP-000001` (who has no manager) fail routing with explicit code `INACTIVE_OR_MISSING_APPROVER` (18 occurrences).
   - Approval authorization boundaries are preserved; self-approval and unauthorized approvers are strictly blocked.

3. **Multi-Request Balance Tracking**:
   - Multiple leave requests across reference year 2026 track cumulative usage against `remaining_days`. 44 requests legitimately exceed balance (`BALANCE_EXCEEDED`).

4. **Corrected V2 Dataset (`instance/phase9_smartflow_v2.db`)**:
   - 800 leave requests: 738 eligible (92.25%), 62 ineligible (7.75%).
   - 1,000 expense claims: 982 eligible, 18 ineligible.
   - 2,965 approval steps, 656 reimbursements ($1,108,341.00 total, $877,614.10 paid), 13,649 workflow events (13,648 original baseline events + 1 audit decision event `EVT-APR-LVE-2026-000028-01-20261101085435477912` from integration verification).
   - 100% two-run reproducibility with matching 64-character SHA-256 table hashes across all 12 entities.

---

## 10. Frontend Architecture (`routes/`, `templates/`, `static/`) — Phase 10

Phase 10 provides a complete, modern, responsive user interface delivering specialized portals for all organizational roles.

### 1. Blueprint Structure
The web application is partitioned into 6 focused Flask blueprints:
- **`main_bp` (`routes/main.py`)**: Root routing, operational executive dashboard, and user persona switching (`/switch-user`).
- **`employee_bp` (`routes/employee.py`)**: Employee self-service hub, leave request submission, multi-line expense claim submission, and request tracking with real-time approval steppers.
- **`approvals_bp` (`routes/approvals.py`)**: Manager approval inbox, request inspection view, and authorized step decision processing.
- **`finance_bp` (`routes/finance.py`)**: Finance operations, reimbursement voucher tracking, disbursement action (`mark_reimbursement_paid`), and cost center budget health analysis.
- **`admin_bp` (`routes/admin.py`)**: Master directory of 400 employees, 20 cost centers, statutory leave and expense policies, approval routing matrix (155 rules), and immutable workflow event audit stream.
- **`api_bp` (`routes/api.py`)**: Client JSON endpoints for real-time business days calculation (`/api/calculate-leave`), cost center budget utilization (`/api/cost-center-budget/<id>`), and country policies (`/api/policies-for-country/<country>`).

### 2. User Persona Switching & RBAC Simulation
- To facilitate interactive demonstration and validation of tiered permissions, the top navigation features an organizational demo persona switcher (`/switch-user`).
- **Demo-Only Feature**: Clearly flagged as a local simulation switcher for evaluation and testing. It does not provide real enterprise authentication or production SSO.
- Evaluators can switch instantly between CEO (`EMP-000001`), Regional Managers (`EMP-000020`, `EMP-000003`, `EMP-000014`), Executives (`EMP-000009`, `EMP-000016`), and individual active employees without altering database credentials or state.
- Global Jinja2 context processor injects the active employee profile, pending approvals count, and organizational dropdown into all rendered templates.

### 3. Design System & Client Scripting
- **Self-Contained Styling (`static/css/style.css`)**: Built with CSS custom properties (variables), flexible responsive grid (`grid-2`, `grid-3`, `grid-4`), metric cards, badges, workflow steppers, progress bars, and alert banners without requiring external CDN dependencies.
- **Client Scripting (`static/js/app.js`)**: Dynamic asynchronous fetch calls for business day calculations excluding statutory holidays, dynamic multi-line item addition/removal for expense claims with automated client-side total recalculation.
- **Error Handling**: Custom branded HTTP 404 (`templates/404.html`) and HTTP 500 (`templates/500.html`) error pages with automatic transaction rollback.

---

## 11. Final Integration, Access Control & Production Readiness — Phase 11

Phase 11 consolidates end-to-end integration, security validation, documentation updates, and complete regression test verification.

### 1. Comprehensive Security & Authorization Safeguards
- **Finance Disbursement Route-Level Authorization**: `POST /finance/reimbursements/<id>/pay` verifies that the active session persona belongs to the `Finance` department (`department == 'Finance'`) or holds an `Executive` role (`role == 'Executive'`). Unauthorized access attempts by general staff or managers from other departments are rejected with an explicit security flash error and redirected.
- **Admin Directory Route-Level Authorization**: All administrative endpoints (`/admin/*`) are safeguarded by a `@admin_bp.before_request` hook that restricts directory access strictly to `Executive` roles (e.g. CEO `EMP-000001`) and `Operations` personnel (`department in {'Operations', 'Executive'}`).
- **Simulated Authentication Disclosure**:
  - The identity system currently utilizes a session-based persona switcher (`/switch-user`) for grading, evaluation, and test simulation.
  - It does **not** provide production-grade authentication (e.g., password hashing with bcrypt/argon2, cryptographic salting, session expiration tokens, MFA, or enterprise SSO integrations such as SAML 2.0 / OpenID Connect / OAuth2).
  - In a production environment, this demo switcher must be replaced with enterprise IdP middleware, secure HTTP-only signed cookies, CSRF protection on all mutating HTTP POST endpoints, and rate limiting.
- **Anti-Self-Approval Enforcement**: Built-in verification prohibiting any employee from reviewing or authorizing their own leave or expense submissions.
- **Strict Approver Authorization**: Checks ensure only the designated approver assigned to a step can execute decisions on that step.
- **Sequential Monotonicity**: Approval steps must be executed strictly in order (1..N); attempts to decide higher steps while lower steps are pending are blocked.
- **Cost Center Budget Overrun Prevention**: Expense claims that exceed the remaining annual budget ceiling of a cost center are rejected with explicit `BUDGET_EXCEEDED` reason codes.
- **Cross-Currency Pollution Prevention**: Strictly enforces canonical country currencies; prevents claims from using mismatched foreign currencies without an explicit FX model.
- **Disbursement Authorization Gate**: Reimbursement vouchers can only be generated for finalized approved claims (`status == 'approved'`) with 100% of required approval steps approved.
- **Input Sanitization & Injection Protection**: Parameterized SQLAlchemy ORM queries, Jinja2 template auto-escaping, and strict date/amount boundary validation.

### 2. Database Safety Guarantee
- **Read-Only Baseline Preservation**: `instance/smartflow.db` (SHA-256: `04e96709...`) and backup `instance/smartflow.db.bak` (SHA-256: `763b55a9...`) remain strictly preserved.
- **Isolated Test Fixtures**: All automated tests execute against temporary, isolated fixture databases with automatic cleanup in `tearDown()`.
- **Protected Database Init**: `init_database()` skips `db.create_all()` when attached to existing production or Phase 9 databases to prevent unintended schema mutations.

### 3. Container & Operational Health
- **Container Health Check Probe (`/health`)**: Exposes JSON telemetry verifying database engine connectivity, table accessibility, and application readiness.
- **Zero-Deprecations WSGI Entry**: Clean entry point in `app.py` compatible with standard production WSGI servers (Gunicorn, Waitress, uWSGI).
