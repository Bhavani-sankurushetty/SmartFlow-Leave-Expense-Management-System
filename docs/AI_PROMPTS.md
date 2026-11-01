# AI Prompts & Interaction History

This document chronicles the actual, verbatim AI user prompts provided during the development and acceptance verification of the **SmartFlow Leave and Expense Request Routing System**.

> [!NOTE]
> **Transparency Statement**: The prompts below represent the actual, unaltered user prompts recorded within the current conversation session and active context window. Phases 1 through 8 were developed in antecedent sessions prior to the current session's retention window; those earlier prompts are documented by their resulting deliverables, architecture milestones, and codebase artifacts. No prompt history has been fabricated or hallucinated.

---

## 1. Phase 9: Budget-Aware Approval Workflow Integration & Reimbursement Lifecycle

```text
Continue the existing SmartFlow Leave and Expense Request Routing System.

This is Phase 9: Budget-Aware Approval Workflow Integration and Reimbursement Lifecycle.

IMPORTANT: This is an existing project. Do not rebuild completed phases or replace existing implementations.

Before editing, thoroughly inspect the existing project structure, database, models, generator, services, tests, and documentation.

### CURRENT IMPLEMENTATION STATUS

The following components already exist and must be preserved:

1. SQLAlchemy models for all 11 core entities.
2. Synthetic data generator:
   - 400 employees
   - 6 leave policies
   - 1,965 leave balances
   - 800 leave requests
   - 20 cost centers
   - 35 expense policies
   - 1,000 expense claims
   - 5,500 expense lines
   - 155 approval rules
3. Leave business logic in `services/leave_service.py`.
4. Expense business logic in `services/expense_service.py`.
5. Approval routing and decision logic in `services/approval_service.py`.
6. Anomaly injection in `generator/anomaly_injector.py`.
7. Ground-truth anomaly manifest.
8. Validation pipeline and reproducibility checks.
9. Existing unit tests.

The current database has zero `approval_steps` and zero `reimbursements`. Approval service logic exists but is not integrated into generated workflow records.

### 1. COST CENTER BUDGET VALIDATION
Implement budget-aware expense validation.
Existing `CostCenter` model has `annual_budget`, but the expense service does not currently enforce it.
- Calculate annual budget utilization by cost center and year.
- Use the country's corresponding currency from existing `COUNTRY_CURRENCIES`.
- Never aggregate amounts across different currencies without an explicit conversion model.
- Verify that employee, claim and cost center countries are consistent.
- Calculate utilized budget from finalized approved claims without double-counting reimbursements.
- Calculate remaining budget.
- Detect claims that exceed the available budget.
- Return structured decisions and reason codes.
- Expose budget used, budget remaining, utilization percentage, and claim impact.
- Non-mutating read-only evaluation.

### 2. END-TO-END WORKFLOW INTEGRATION
Connect request evaluation, dynamic approval routing and lifecycle simulation.
- Route requests through `approval_service.py` based on tiered rules.
- Filter ineligible requests before creating approval steps.
- Create concrete `ApprovalStep` records in database.
- Process approval decisions sequentially.
- Record auditable history for every transition.
- Generate deterministic sequential step IDs.
- Preserve foreign key and check constraints.

### 3. REIMBURSEMENT LIFECYCLE
Implement reimbursement creation and payment tracking for approved claims.
- Create concrete `Reimbursement` records only for approved claims.
- Do not create reimbursements for rejected or pending claims.
- Prevent duplicate reimbursements for the same claim.
- Track reimbursement status: `pending`, `paid`.
- Validate that payment date is not earlier than creation date.
- Record transition history.
- Generate deterministic IDs: `RMB-YYYY-NNNNNN`.
- Preserve database safety: do not overwrite `instance/smartflow.db` or `instance/smartflow.db.bak`.
```

---

## 2. Phase 9: Final Acceptance Audit

```text
Perform a FINAL ACCEPTANCE AUDIT of the existing SmartFlow Phase 9 implementation.

IMPORTANT: Phase 9 implementation is already completed. Do not rebuild or refactor it. Do not start Phase 10 or frontend development.

Inspect the actual implementation, generated database and existing test results.

### 1. Leave Eligibility Distribution
The Phase 9 report states:
- Total leave requests: 800
- Eligible: 154
- Ineligible: 646

Provide an actual reason-wise breakdown of the 646 ineligible requests.
Check whether the high ineligibility rate is caused by:
- Insufficient leave balance
- Invalid employment dates
- Policy mismatch
- Overlapping leave
- Invalid date ranges
- Weekend/holiday calculations
- Other eligibility rules

Determine whether this distribution is realistic and consistent with the documented generator logic.
Do not change the dataset merely to improve the percentages.

### 2. Generator Workflow Integration
Verify actual records in `instance/phase9_smartflow.db`.
Report:
- Total leave requests
- Total expense claims
- Total approval steps
- Approved, rejected and pending approval steps
- Approved, rejected and pending leave requests
- Approved, rejected and pending expense claims
- Total reimbursements
- Pending reimbursements
- Paid reimbursements

Verify that approval steps and reimbursements are actually generated through business workflow services, not fabricated independently.

### 3. Reproducibility Audit
Generate two independent temporary databases using the same seed `20261022` and identical configuration.
Compare:
- Row counts for all 12 entities
- Approval decisions
- Approver assignments
- Approval step sequences
- Reimbursement IDs and amounts
- Workflow event records
- SHA-256 checksums of exported table data

Report exact reproducibility results. Do not overwrite `phase9_smartflow.db` or existing databases.

### 4. Automated Test Coverage
Inspect whether the existing 97-test suite directly tests generator integration.
Verify:
- Generated approval steps exist after workflow simulation.
- Only fully approved claims receive reimbursements.
- Rejected and pending claims are not reimbursed.
- Deterministic workflow simulation is reproducible.
- Budget calculations reconcile.
- Workflow history is persisted.
- All seven required edge scenarios are covered.
- Ground-truth labels are not used in operational decision logic.
```

---

## 3. Phase 9.1: Leave Data Quality Correction & Workflow Simulation

```text
Continue SmartFlow Phase 9. Perform a focused Phase 9.1 – Leave Data Quality Correction and Final Reproducibility Audit.

Phase 9 backend implementation is already complete. Do not rebuild it, and do not start Phase 10.

### 1. Correct Leave Policy Assignment
The final audit identified:
- 800 leave requests.
- 154 eligible requests.
- 646 ineligible requests.
- 642 failures caused by COUNTRY_POLICY_MISMATCH.
- 4 failures caused by INACTIVE_OR_MISSING_APPROVER.

Root cause: `eligible_policies_for_employee()` selects policies without adequately filtering for employee country.

Fix the data generator so that:
1. Normal leave requests use policies matching the employee's country.
2. Leave category selection only uses policies available to that employee's country.
3. Preserve all 6 existing leave policies.
4. Preserve employee hierarchy, policy effective dates and leave balances.
5. Preserve business-day and holiday calculations.
6. Do not generate artificial policy mismatch records as normal baseline requests.
7. Any deliberately mismatched policy scenario must have an explicit `issue_type` and separate ground-truth label.
8. Do not silently change operational evaluation logic to hide country mismatches.

### 2. Approval Hierarchy Exceptions
Investigate the four remaining routing failures:
- Three requests have an inactive direct manager.
- One request belongs to the CEO, who has no manager.
Determine whether a valid escalation path to an authorized active approver exists.
If no authorized approver exists, preserve it as an explicit `INACTIVE_OR_MISSING_APPROVER` exception.
Never bypass approval authorization or permit self-approval just to increase approval rates.

### 3. Preserve Required Edge Scenarios
Ensure all seven original exception scenarios remain covered:
1. Negative leave balance.
2. Overlapping approved leave for the same employee.
3. Expense date outside employment dates.
4. Split claims intended to evade thresholds.
5. Missing cost center.
6. Approval by requester.
7. Duplicate reimbursement.
Keep these as deliberately labelled cases where applicable.

### 4. Generate a New Isolated Dataset
CRITICAL: Do not overwrite existing databases.
Preserve:
- `instance/smartflow.db`
- `instance/smartflow.db.bak`
- `instance/phase9_smartflow.db`

Generate the corrected dataset in:
- `instance/phase9_smartflow_v2.db`
```

---

## 4. Submission Packaging & Workspace Verification Directive

```text
No ZIP file is required. Keep the project in its existing normal folder:

C:\Users\Karthik\Desktop\SmartFlow

Do not create a ZIP or duplicate the entire project.

Just verify that the existing project folder contains all required source code, corrected V2 database (`instance/phase9_smartflow_v2.db`), README, requirements.txt, schema, data dictionary, and validation reports.

Check that the README has correct setup and run instructions. Preserve all original databases. Report any missing submission files and their exact paths.

Do not start Phase 10 or add new features.
```

---

## 5. Phase 10 & Phase 11: Frontend Implementation & Production Readiness

```text
We have successfully completed Phase 9.1. Now proceed with Phase 10 and Phase 11 before final submission.

Phase 10 – Frontend Implementation
Build a complete frontend for the existing SmartFlow system:
- Employee portal: submit and track leave requests and expense claims.
- Manager/Approver portal: approval inbox, request details, approve/reject actions.
- Finance portal: approved expenses, reimbursement status and payment tracking.
- Admin portal: employees, policies, approval rules and configuration.
- Dashboard: leave/expense statistics, pending approvals, budget utilization and workflow metrics.

Requirements:
- Use the existing Flask application, database models and business services.
- Integrate with existing workflow, approval, leave, expense and reimbursement logic.
- Do not duplicate business rules in frontend code.
- Use a clean, professional, responsive UI.
- Add proper validation, error handling and role-based access control.
- Test all major user workflows.

Phase 11 – Final Integration and Production Readiness
First inspect the existing project roadmap and documentation to identify the originally intended Phase 11 scope. Do not invent or skip requirements.

Complete the defined Phase 11 work, including integration checks, security checks, workflow verification, documentation updates and final regression testing where applicable.

Database safety – mandatory
- Never overwrite or reset `instance/smartflow.db`.
- Never overwrite `instance/smartflow.db.bak`.
- Never overwrite `instance/phase9_smartflow.db`.
- Preserve `instance/phase9_smartflow_v2.db`.
- Use an isolated development/test database for frontend testing and migrations.
- Never delete existing project files or tests.

Work sequentially: finish Phase 10, verify it, then Phase 11, and verify the complete application.

At the end, provide:
1. Features implemented in both phases.
2. Exact commands to run the application.
3. Test results and known issues.
4. Final project folder structure.
5. Whether the project is ready for submission.

Do not claim completion until the relevant tests have actually passed.
```

---

## 6. Final Submission Safety Audit Directive

```text
FINAL SUBMISSION SAFETY AUDIT – NO NEW FEATURES

Phase 10 and Phase 11 implementation is reported complete. Perform only these final checks:

1. Inspect `config.py` and `app.py`. Confirm which exact SQLite database the Flask application opens by default. Ensure the application uses `instance/phase9_smartflow_v2.db` and never silently falls back to `instance/smartflow.db`.
2. Inspect role and access-control implementation. Clearly distinguish actual authentication and authorization from the demo persona switcher. Test that unauthorized users cannot perform approval, finance, or admin actions.
3. Verify the employee, manager, finance, and admin routes using the test client. Check important page loads and representative actions without modifying the submitted V2 database.
4. Verify the SHA-256 hashes of all four database files and compare them against their recorded expected hashes. Do not modify or regenerate any database.
5. Create `docs/AI_PROMPTS.md` using actual prompts available in the project conversation/context. Do not fabricate prompt history. Explain any unavailable history honestly.
6. Confirm README run instructions, dependencies, and database configuration are correct.

Do not add features, change business logic, reset databases, or rerun dataset generation.

Return a concise final report with PASS/FAIL for each check and any remaining submission blockers.
```
