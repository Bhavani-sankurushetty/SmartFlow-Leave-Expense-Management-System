# Data Dictionary

Master and transaction data entities generated in Phase 3 and Phase 4. Field names match the SQLAlchemy models. Values in parentheses are the business names used in generation requirements.

## Common rules

- Primary keys are deterministic strings.
- Foreign keys must reference an existing parent row.
- `created_at` / `updated_at` are UTC timestamps managed by the application.
- Master and transaction generation uses seed `20261022`.
- `remaining_days` is always derived: `opening_balance + accrued_days - used_days`.
- Baseline master balances are non-negative.
- Baseline leave requests do not overlap for the same employee and do not exceed available balances.
- ExpenseClaim totals are strictly reconciled: `ExpenseClaim.total_amount == SUM(ExpenseLine.amount)`.
- Line items with amount >= `receipt_required_above` have synthetic receipt filename and unique SHA-256 hash metadata.

## CostCenter (`cost_centers`)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| cost_center_id | string PK | yes | `CC-000001` |
| code | string unique | yes | Business code such as `CC-IND-ENG` |
| name | string | yes | `{country} {department}` |
| country | string | yes | India, USA, UK, Canada, Australia |
| annual_budget | numeric(14,2) | yes | Realistic annual budget |
| owner_employee_id | string FK → employees | yes after generation | Assigned after employees exist |
| is_active | boolean | yes | Master cost centers are active |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

Validation: unique IDs and codes; owner must be a valid employee; 20 cost centers.

## Employee (`employees`)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| employee_id | string PK | yes | `EMP-000001` |
| first_name | string | yes | Faker, locale matches country |
| last_name | string | yes | Faker, locale matches country |
| email | string unique | yes | `{first}.{last}.{seq}@smartflow.example` |
| country | string | yes | One of the five supported countries |
| department | string | yes | Engineering, Finance, HR, Sales, Marketing, Operations, Legal, Product, Customer Support, Administration |
| job_title | string | yes | CEO, VP, director, manager, or individual contributor |
| manager_id | string FK → employees | no | Null only for the CEO |
| cost_center_id | string FK → cost_centers | yes | Must match a generated cost center |
| hire_date | date | yes | On or before 2026-10-31 |
| termination_date | date | no | Set only when `is_active` is false |
| is_active | boolean | yes | Active status |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

Validation: 40 employees (starter) or 400 (full); unique emails; no self-management; no manager cycles; manager IDs exist; cost center IDs exist.

## LeavePolicy (`leave_policies`)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| leave_policy_id | string PK | yes | `LPOL-000001` |
| name | string | yes | `{country} {leave_type}` |
| leave_type | string | yes | Annual, Sick, Casual, Maternity, Paternity, Bereavement |
| country | string | yes | Statutory template country |
| annual_allowance_days | numeric(8,2) | yes | Requirement name: annual_allowance |
| max_consecutive_days | numeric(8,2) | no | Maximum consecutive days per request |
| min_notice_days | integer | yes | Requirement name: minimum_notice_days |
| carryover_days | numeric(8,2) | yes | `0` means carryover is not allowed |
| is_active | boolean | yes | Active status |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

Validation: exactly 6 policies; one of each leave type.

## LeaveBalance (`leave_balances`)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| leave_balance_id | string PK | yes | `LBAL-000001` |
| employee_id | string FK → employees | yes | |
| leave_policy_id | string FK → leave_policies | yes | |
| year | integer | yes | `2026` |
| opening_balance | numeric(8,2) | yes | Carryover, or 0 if hired in 2026 |
| accrued_days | numeric(8,2) | yes | Allowance × months employed in 2026 / 12 |
| used_days | numeric(8,2) | yes | From available days; never exceeds available |
| remaining_days | numeric(8,2) | yes | Derived: opening + accrued - used |
| as_of_date | date | yes | 2026-10-31 |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

Unique constraint: `(employee_id, leave_policy_id, year)`.

## ExpensePolicy (`expense_policies`)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| expense_policy_id | string PK | yes | `EPOL-000001` |
| name | string | yes | `{country} {category} Policy` |
| category | string | yes | Travel, Accommodation, Meals, Transportation, Office Supplies, Software, Training |
| country | string | yes | Country-specific limits |
| max_amount | numeric(14,2) | yes | Base limit × country multiplier |
| receipt_required_above | numeric(14,2) | no | Requirement name: receipt_threshold |
| is_active | boolean | yes | Active status |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

One policy per country and category (35 rows).

## ApprovalRule (`approval_rules`)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| approval_rule_id | string PK | yes | `ARUL-000001` |
| request_type | string | yes | `leave` or `expense` |
| country | string | no | Country for approval scope |
| category | string | no | Null for leave; expense category for expense rules |
| min_amount | numeric(14,2) | no | Inclusive lower bound (days for leave, amount for expense) |
| max_amount | numeric(14,2) | no | Inclusive upper bound; null means unbounded |
| authority_level | integer | yes | 1 Manager, 2 Department Head, 3 Finance, 4 Senior Management |
| step_order | integer | yes | Increasing within a country/category group |
| is_active | boolean | yes | Active status |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

## LeaveRequest (`leave_requests`) — Phase 4

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| leave_request_id | string PK | yes | `LVE-2026-000001` |
| employee_id | string FK → employees | yes | Employee submitting the request |
| leave_policy_id | string FK → leave_policies | yes | Policy for the requested leave type |
| start_date | date | yes | First day of leave (business day in 2026) |
| end_date | date | yes | Last day of leave (business day in 2026; >= start_date) |
| business_days | numeric(8,2) | yes | Exactly computed: weekdays excluding weekends and country public holidays |
| status | string | yes | `submitted`, `approved`, `rejected` |
| submitted_at | datetime | yes | UTC timestamp before start_date respecting notice |
| decided_at | datetime | no | UTC timestamp if decided; null if submitted |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

Validation rules:
- Exactly 800 requests (full) or 80 requests (starter).
- Start date and end date fall within the employee's active tenure in 2026.
- Business days exclude Saturday, Sunday, and documented statutory public holidays for employee's country.
- Approved leave date ranges do not overlap for any individual employee.
- Cumulative approved days do not exceed available leave balances.

## ExpenseClaim (`expense_claims`) — Phase 4

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| expense_claim_id | string PK | yes | `EXP-2026-000001` |
| employee_id | string FK → employees | yes | Submitting employee |
| cost_center_id | string FK → cost_centers | yes | Employee's active cost center |
| expense_policy_id | string FK → expense_policies | yes | Policy matching employee country and category |
| claim_date | date | yes | Date of submission in 2026 |
| country | string | yes | Matches employee country |
| currency | string | yes | Local currency (`USD`, `INR`, `GBP`, `CAD`, `AUD`) |
| total_amount | numeric(14,2) | yes | Strictly reconciled: `SUM(ExpenseLine.amount)` |
| status | string | yes | `submitted`, `approved`, `rejected` |
| submitted_at | datetime | yes | UTC timestamp on claim_date |
| decided_at | datetime | no | UTC timestamp if approved/rejected; null if submitted |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

Validation rules:
- Exactly 1,000 claims (full) or 100 claims (starter).
- Every claim has between 1 and 10 associated ExpenseLines.
- `total_amount` must equal the exact sum of line amounts. Never independently generated.
- `total_amount` is within the associated `ExpensePolicy.max_amount`.

## ExpenseLine (`expense_lines`) — Phase 4

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| expense_line_id | string PK | yes | `EXPL-EXP-2026-000001-01` |
| expense_claim_id | string FK → expense_claims | yes | Parent expense claim |
| line_number | integer | yes | Sequential index (1..10) within the claim |
| category | string | yes | Matches parent claim's expense category |
| description | string | yes | Detailed business expense description with embedded receipt metadata |
| expense_date | date | yes | On or before claim_date, within employment |
| amount | numeric(14,2) | yes | Line expenditure amount |
| country | string | yes | Matches parent claim's country |
| created_at | datetime | yes | UTC timestamp |
| updated_at | datetime | yes | UTC timestamp |

Receipt metadata rules:
- If `amount >= policy.receipt_required_above`, a synthetic receipt filename `receipt_{claim_id}_{line_number:02d}.pdf` and unique SHA-256 hash are generated.
- The metadata is captured in the line description: `"{desc} [Receipt: {filename} | SHA256: {hash[:16]}]"`.
- All baseline receipt hashes are verified 100% unique.

## Leave Decision Output (`evaluate_leave_request`) — Phase 5

Structured dictionary returned by the leave evaluation service:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| request_id | string | no | ID of the request evaluated, or None |
| employee_id | string | yes | Submitting employee ID |
| decision | string | yes | `APPROVE`, `REJECT`, `NEEDS_REVIEW` |
| reason_code | string | yes | Programmatic decision code (see below) |
| reason | string | yes | Human-readable explanation |
| requested_days | numeric(8,2) | yes | Business days requested |
| available_days | numeric(8,2) | yes | Available balance days at time of evaluation |
| policy_limit | numeric(8,2) | no | Policy consecutive days limit |
| evaluated_at | datetime | yes | UTC timestamp of evaluation |

### Decision Codes

- `EMPLOYEE_NOT_FOUND`: Employee ID does not exist in database.
- `POLICY_NOT_FOUND`: Leave policy ID does not exist or is inactive.
- `COUNTRY_POLICY_MISMATCH`: Employee country does not match policy country.
- `INVALID_DATE_RANGE`: Start date after end date or 0 business days.
- `OUTSIDE_EMPLOYMENT_PERIOD`: Leave dates fall outside employee hire/termination dates.
- `POLICY_LIMIT_EXCEEDED`: Requested days exceed policy `max_consecutive_days`.
- `OVERLAPPING_APPROVED_LEAVE`: Overlaps with an already approved leave request.
- `NO_AVAILABLE_BALANCE`: Zero available balance for this policy.
- `BALANCE_EXCEEDED`: Requested days exceed available balance.
- `ELIGIBLE_FOR_APPROVAL`: All checks passed. Returned with `NEEDS_REVIEW` (when manager review is required) or `APPROVE` (when approved/auto-approvable).

## Expense Decision Output (`evaluate_expense_claim`) — Phase 6

Structured dictionary returned by the expense evaluation service:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| claim_id | string | yes | ID of the claim evaluated |
| employee_id | string | yes | Submitting employee ID |
| decision | string | yes | `APPROVE`, `REJECT`, `NEEDS_REVIEW` |
| reason_code | string | yes | Programmatic decision code (see below) |
| reason | string | yes | Human-readable explanation |
| claim_amount | numeric(14,2) | yes | Total claim amount |
| policy_limit | numeric(14,2) | yes | Applicable policy max_amount |
| country | string | yes | Claim country |
| category | string | yes | Policy category |
| evaluated_at | datetime | yes | UTC timestamp of evaluation |

### Expense Decision Codes

- `EMPLOYEE_NOT_FOUND`: Employee ID does not exist in database.
- `POLICY_NOT_FOUND`: Expense policy ID does not exist or is inactive.
- `INVALID_EMPLOYMENT_DATE`: Employee hire date is missing or invalid.
- `EXPENSE_DATE_OUTSIDE_EMPLOYMENT`: Claim date or line date falls outside active employment tenure.
- `COUNTRY_POLICY_MISMATCH`: Policy country does not match employee country or claim country.
- `CATEGORY_POLICY_MISMATCH`: Line category does not match policy category.
- `COST_CENTER_NOT_FOUND`: Cost center ID does not exist or is inactive.
- `INVALID_LINE_COUNT`: Claim does not have between 1 and 10 lines.
- `INVALID_EXPENSE_DATE`: Line date is missing or after claim date.
- `INVALID_EXPENSE_AMOUNT`: Line amount is negative or zero.
- `CLAIM_TOTAL_MISMATCH`: Claim total does not equal the exact sum of line amounts.
- `POLICY_LIMIT_EXCEEDED`: Claim total exceeds policy `max_amount`.
- `RECEIPT_REQUIRED`: Line amount $\ge$ `receipt_required_above` but missing receipt metadata.
- `DUPLICATE_RECEIPT`: Receipt hash is duplicated within the claim or across claims.
- `ELIGIBLE_FOR_APPROVAL`: All checks passed. Returned with `NEEDS_REVIEW` (manager review required).

## Approval Routing & Steps (`services/approval_service`) — Phase 7

### Routing Decision Codes (`resolve_approval_route`)

- `VALID`: Valid approval route resolved with active, sequential approvers.
- `INELIGIBLE`: Request failed eligibility check in leave or expense service.
- `NO_MATCHING_RULE`: Amount or country/category does not match any active approval rule.
- `INACTIVE_OR_MISSING_APPROVER`: Required approver for an authority level is missing, inactive, or self-approving.
- `CONFLICTING_RULES`: Multiple conflicting active rules cover the same amount.
- `NOT_FOUND`: Target request does not exist in database.

### Approval Step Decision Statuses (`ApprovalStep.decision`)

- `pending`: Step created and awaiting action from the assigned approver.
- `approved`: Step approved by the authorized approver.
- `rejected`: Step rejected by the authorized approver (halts request and marks parent rejected).

## Anomaly Injection & Ground Truth (`generator/anomaly_injector`) — Phase 8

### Supported Anomaly Categories

- `EXPENSE_POLICY_LIMIT_EXCEEDED`: Claim total exceeds policy limit (`POLICY_LIMIT_EXCEEDED`).
- `EXPENSE_MISSING_RECEIPT`: Line amount $\ge$ threshold but receipt tag removed (`RECEIPT_REQUIRED`).
- `EXPENSE_DUPLICATE_RECEIPT_HASH`: Reused receipt hash from another claim (`DUPLICATE_RECEIPT`).
- `EXPENSE_CLAIM_TOTAL_MISMATCH`: Line sum does not match claim `total_amount` (`CLAIM_TOTAL_MISMATCH`).
- `EXPENSE_NEGATIVE_AMOUNT`: Line amount $\le 0.00$ (`INVALID_EXPENSE_AMOUNT`).
- `EXPENSE_DATE_OUTSIDE_EMPLOYMENT`: Claim date before employee hire date (`EXPENSE_DATE_OUTSIDE_EMPLOYMENT`).
- `EXPENSE_CATEGORY_MISMATCH`: Line category violates policy category (`CATEGORY_POLICY_MISMATCH`).
- `LEAVE_BALANCE_EXCEEDED`: Requested leave business days exceed available balance (`BALANCE_EXCEEDED`).
- `LEAVE_CONSECUTIVE_DAYS_EXCEEDED`: Requested days exceed policy `max_consecutive_days` (`POLICY_LIMIT_EXCEEDED`).
- `LEAVE_DATE_OUTSIDE_EMPLOYMENT`: Leave start date before hire date (`OUTSIDE_EMPLOYMENT_PERIOD`).
- `LEAVE_COUNTRY_POLICY_MISMATCH`: Foreign policy assigned to employee (`COUNTRY_POLICY_MISMATCH`).
- `APPROVAL_INACTIVE_APPROVER`: Required approver is inactive (`INACTIVE_OR_MISSING_APPROVER`).
- `APPROVAL_UNAUTHORIZED_APPROVER`: Non-assigned approver attempts decision (`UNAUTHORIZED_APPROVER`).
- `APPROVAL_OUT_OF_ORDER`: Deciding step 2 while step 1 is pending (`PENDING_PRIOR_STEPS`).

### Ground Truth Manifest Structure (`ground_truth/anomaly_manifest.json`)

- `anomaly_id`: Deterministic ID (`ANOM-000001` or `NORM-000015`).
- `anomaly_category`: Specific anomaly category name.
- `entity_type`: `ExpenseClaim`, `ExpenseLine`, `LeaveRequest`, or `ApprovalStep`.
- `entity_id`: Target entity primary key.
- `target_field`: Field modified.
- `original_value`: Exact pre-injection snapshot.
- `injected_value`: Injected anomalous value.
- `reason`: Explanation of the injected violation.
- `expected_detection_code`: Code expected from the relevant business service.
- `ground_truth_label`: `ANOMALOUS` or `NORMAL`.

## Reimbursement (`reimbursements`) — Phase 9

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `reimbursement_id` | string PK | yes | Deterministic ID format `RMB-YYYY-NNNNNN` |
| `expense_claim_id` | string FK → expense_claims | yes | Unique constraint `uq_reimbursement_expense_claim` (1:1 with claim) |
| `amount` | numeric(14,2) | yes | Must strictly equal `ExpenseClaim.total_amount` |
| `currency` | string(3) | yes | 3-letter ISO matching country currency |
| `payment_method` | string | yes | `direct_deposit`, `bank_transfer`, or `check` |
| `status` | string | yes | `pending` or `paid` |
| `created_at` | datetime | yes | UTC timestamp of reimbursement creation |
| `paid_at` | datetime | no | UTC timestamp when marked paid; strictly `paid_at >= created_at` |

Validation:
- Created strictly after final approval of all required `approval_steps`.
- Exactly one reimbursement per expense claim.
- Reject attempts to reimburse pending, rejected, or draft claims.
- Payment transition is idempotent and irreversible.

## Workflow Event Audit Log (`workflow_events`) — Phase 9

Append-only audit model recording every meaningful state transition and business evaluation.

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `event_id` | string PK | yes | Deterministic ID format `EVT-YYYY-NNNNNN` or UUID |
| `request_type` | string(32) | yes | `leave` or `expense` |
| `entity_id` | string(32) | yes | ID of parent `LeaveRequest` or `ExpenseClaim` |
| `event_type` | string(64) | yes | Event name (e.g., `REQUEST_SUBMITTED`, `STEP_DECIDED`, `REIMBURSEMENT_PAID`) |
| `actor_employee_id` | string FK → employees | no | Approver or system actor triggering the transition |
| `from_status` | string(32) | no | Pre-transition status |
| `to_status` | string(32) | no | Post-transition status |
| `payload_json` | text | no | Serialized JSON with evaluation reason codes, amounts, or audit metadata |
| `created_at` | datetime | yes | UTC timestamp |

## Cost Center Budget Validation — Phase 9

### Utilization Calculation
$$\text{budget\_used} = \sum_{\substack{c \in \text{ApprovedClaims} \\ c.\text{cost\_center\_id} = CC \\ c.\text{claim\_date}.\text{year} = Y}} c.\text{total\_amount}$$

$$\text{budget\_remaining} = \max(0.00, \text{annual\_budget} - \text{budget\_used})$$

$$\text{utilization\_pct} = \frac{\text{budget\_used}}{\text{annual\_budget}} \times 100$$

### Decision Codes (`evaluate_expense_claim`)
- `BUDGET_EXCEEDED`: Claim total amount exceeds available budget remaining (`requested_amount > budget_remaining`).
- `BUDGET_EXHAUSTED`: Cost center annual budget is already fully utilized (`budget_remaining <= 0.00`).
- `COUNTRY_COST_CENTER_MISMATCH`: Cost center country does not match employee or claim country.
- `CURRENCY_MISMATCH`: Claim currency does not match cost center country currency.

