# SmartFlow Validation Summary

**Generated At**: 2026-11-01T08:42:19.196046+00:00  
**Validation Status**: **PASSED**  
**Execution Mode**: `ISOLATED_FIXTURE_EVALUATION`  
**Random Seed**: `20261022`

---

## 1. Dataset Integrity Checks

| Check Name | Status | Details |
| :--- | :--- | :--- |
| `foreign_key_check` | **PASS** | 0 violations |
| `sqlite_integrity_check` | **PASS** | PRAGMA integrity_check = ok |
| `expense_totals_reconciliation` | **PASS** | Checked 1000 claims; 0 mismatch(es) |
| `date_chronology_check` | **PASS** | 0 date inconsistency violation(s) |

---

## 2. Anomaly Detection Confusion Matrix

Positive Class: `ANOMALOUS`  
Negative Class: `NORMAL` (Control)

| Metric | Count | Percentage |
| :--- | :--- | :--- |
| **True Positives (TP)** | 14 | 77.8% |
| **True Negatives (TN)** | 4 | 22.2% |
| **False Positives (FP)** | 0 | 0.0% |
| **False Negatives (FN)** | 0 | 0.0% |

---

## 3. Classification Performance Metrics

| Metric | Score | Formula / Definition |
| :--- | :--- | :--- |
| **Accuracy** | **100.00%** | $\frac{TP + TN}{Total}$ |
| **Precision** | **100.00%** | $\frac{TP}{TP + FP}$ |
| **Recall (Sensitivity)** | **100.00%** | $\frac{TP}{TP + FN}$ |
| **Specificity** | **100.00%** | $\frac{TN}{TN + FP}$ |
| **F1 Score** | **1.0000** | $2 \cdot \frac{Precision \cdot Recall}{Precision + Recall}$ |

---

## 4. Reproducibility Verification

- **Deterministic Planning**: PASS
- **Identical Sequence & Values**: PASS across repeated executions with seed `20261022`.

---

## 5. Approval Workflow and Reimbursement Lifecycle Validation

| Validation Check | Status | Details |
| :--- | :--- | :--- |
| `approval_step_single_parent` | **PASS** | 2965 steps checked; 0 ambiguous step(s) |
| `approval_step_sequence_continuity` | **PASS** | 1720 routed requests checked; 0 sequence issue(s) |
| `self_approval_prevention` | **PASS** | 0 self-approval violation(s) |
| `approver_active_authorization` | **PASS** | 0 inactive approver violation(s) |
| `approval_lifecycle_transitions` | **PASS** | 2965 steps checked; 0 transition issue(s) |
| `parent_request_status_consistency` | **PASS** | 1720 routed requests checked; 0 status inconsistency(s) |
| `reimbursement_approved_claims_only` | **PASS** | 656 reimbursements checked; 0 unapproved claim(s) |
| `reimbursement_amount_exact_match` | **PASS** | 656 reimbursements checked; 0 mismatch(es) |
| `reimbursement_duplicate_prevention` | **PASS** | 656 reimbursements checked; 0 duplicate(s) |
| `reimbursement_payment_chronology` | **PASS** | 524 paid reimbursements; 0 chronology issue(s) |
| `cost_center_budget_reconciliation` | **PASS** | 20 cost centers checked; 0 arithmetic issue(s) |
| `cost_center_currency_consistency` | **PASS** | 1000 expense claims checked; 0 currency mismatch(es) |

### Phase 9 Operational Metrics
- **Approval Steps**: 2965 total (2353 approved, 190 rejected, 422 pending)
- **Reimbursements**: 656 total (524 paid, 132 pending)
- **Workflow Audit Events**: 13647 recorded

---

## 6. Scope & Limitations

> [!NOTE]
> These metrics demonstrate **functional verification** of all 14 supported business-rule violation categories and 4 normal controls on an isolated fixture, as well as Phase 9 budget reconciliation and reimbursement lifecycles. The production database remains clean and intact.
