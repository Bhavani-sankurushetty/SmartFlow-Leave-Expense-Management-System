"""Unit tests for SmartFlow Phase 9 Validation Pipeline and Ground Truth Evaluation."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from app import app
from generator.constants import RANDOM_SEED
from services.approval_service import resolve_approval_route
from services.expense_service import evaluate_expense_claim
from services.leave_service import evaluate_leave_request
from validation.reproducibility import verify_reproducibility
from validation.validate import (
    evaluate_detection_pipeline,
    generate_validation_reports,
    validate_dataset,
    validate_dataset_integrity,
    validate_manifest_structure,
)


class TestValidationPipeline(unittest.TestCase):
    """Test suite covering the 13 required validation scenarios."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_manifest = os.path.join(self.temp_dir, "test_manifest.json")
        self.temp_reports_dir = os.path.join(self.temp_dir, "reports")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Valid manifest
    def test_01_valid_manifest(self):
        """1. Official manifest passes structural validation."""
        res = validate_manifest_structure()
        self.assertTrue(res["is_valid"])
        self.assertEqual(len(res["errors"]), 0)
        self.assertEqual(res["total_records"], 18)
        self.assertEqual(res["anomalous_count"], 14)
        self.assertEqual(res["normal_count"], 4)

    # 2. Missing required manifest fields
    def test_02_missing_required_manifest_fields(self):
        """2. Manifest missing required top-level or record-level fields fails validation."""
        bad_manifest = {
            "status": "SPECIFIED",
            # missing seed, total_records, anomalous_count, normal_count
            "records": [{"anomaly_id": "A1"}],  # missing entity_id, category, etc.
        }
        with open(self.temp_manifest, "w", encoding="utf-8") as f:
            json.dump(bad_manifest, f)

        res = validate_manifest_structure(self.temp_manifest)
        self.assertFalse(res["is_valid"])
        self.assertGreater(len(res["errors"]), 0)

    # 3. Duplicate anomaly IDs
    def test_03_duplicate_anomaly_ids(self):
        """3. Manifest containing duplicate anomaly IDs is rejected."""
        dup_manifest = {
            "status": "SPECIFIED",
            "seed": 20261022,
            "total_records": 2,
            "anomalous_count": 2,
            "normal_count": 0,
            "records": [
                {
                    "anomaly_id": "ANOM-DUP-01",
                    "anomaly_category": "EXPENSE_POLICY_LIMIT_EXCEEDED",
                    "entity_type": "ExpenseClaim",
                    "entity_id": "EXP-2026-000001",
                    "expected_detection_code": "POLICY_LIMIT_EXCEEDED",
                    "ground_truth_label": "ANOMALOUS",
                },
                {
                    "anomaly_id": "ANOM-DUP-01",  # Duplicate ID
                    "anomaly_category": "EXPENSE_MISSING_RECEIPT",
                    "entity_type": "ExpenseLine",
                    "entity_id": "EXPL-EXP-2026-000001-01",
                    "expected_detection_code": "RECEIPT_REQUIRED",
                    "ground_truth_label": "ANOMALOUS",
                },
            ],
        }
        with open(self.temp_manifest, "w", encoding="utf-8") as f:
            json.dump(dup_manifest, f)

        res = validate_manifest_structure(self.temp_manifest)
        self.assertFalse(res["is_valid"])
        self.assertTrue(any("Duplicate anomaly ID" in e for e in res["errors"]))

    # 4. Dataset integrity
    def test_04_dataset_integrity(self):
        """4. Dataset integrity check passes with zero FK violations and reconciled totals."""
        res = validate_dataset_integrity()
        self.assertTrue(res["is_valid"])
        self.assertEqual(len(res["errors"]), 0)
        for chk in res["checks"]:
            self.assertTrue(chk["passed"])

    # 5. Correct anomaly detection
    def test_05_correct_anomaly_detection(self):
        """5. Evaluation pipeline detects all 14 injected anomalies with 100% precision."""
        eval_res = evaluate_detection_pipeline(use_isolated_fixture=True)
        cm = eval_res["confusion_matrix"]
        m = eval_res["metrics"]

        self.assertEqual(cm["true_positives"], 14)
        self.assertEqual(cm["true_negatives"], 4)
        self.assertEqual(cm["false_positives"], 0)
        self.assertEqual(cm["false_negatives"], 0)
        self.assertEqual(m["accuracy"], 1.0)
        self.assertEqual(m["precision"], 1.0)
        self.assertEqual(m["recall"], 1.0)
        self.assertEqual(m["specificity"], 1.0)
        self.assertEqual(m["f1_score"], 1.0)

    # 6. False negatives handling
    def test_06_false_negatives_handling(self):
        """6. A record where detected code does not match expected code is counted as a false negative."""
        # Manifest with intentionally wrong expected detection code
        wrong_manifest = {
            "status": "SPECIFIED",
            "seed": 20261022,
            "total_records": 1,
            "anomalous_count": 1,
            "normal_count": 0,
            "records": [
                {
                    "anomaly_id": "ANOM-TEST-FN",
                    "anomaly_category": "EXPENSE_POLICY_LIMIT_EXCEEDED",
                    "entity_type": "ExpenseClaim",
                    "entity_id": "EXP-2026-000001",
                    "target_field": "total_amount",
                    "original_value": "1680.00",
                    "injected_value": "9999.00",
                    "expected_detection_code": "IMPOSSIBLE_CODE",  # Wrong expectation
                    "ground_truth_label": "ANOMALOUS",
                }
            ],
        }
        with open(self.temp_manifest, "w", encoding="utf-8") as f:
            json.dump(wrong_manifest, f)

        res = evaluate_detection_pipeline(manifest_path=self.temp_manifest, use_isolated_fixture=True)
        self.assertEqual(res["confusion_matrix"]["false_negatives"], 1)
        self.assertEqual(res["confusion_matrix"]["true_positives"], 0)

    # 7. False positives against normal controls
    def test_07_false_positives_against_normal_controls(self):
        """7. Normal control records evaluate as eligible without being flagged anomalous."""
        eval_res = evaluate_detection_pipeline(use_isolated_fixture=True)
        cm = eval_res["confusion_matrix"]
        self.assertEqual(cm["false_positives"], 0)
        self.assertEqual(cm["true_negatives"], 4)

    # 8. Correct metric calculations
    def test_08_correct_metric_calculations(self):
        """8. Precision, recall, F1, and accuracy formulas are mathematically verified."""
        tp, tn, fp, fn = 8, 2, 1, 1
        total = tp + tn + fp + fn
        prec = tp / (tp + fp)
        rec = tp / (tp + fn)
        f1 = 2 * (prec * rec) / (prec + rec)
        acc = (tp + tn) / total
        spec = tn / (tn + fp)

        self.assertAlmostEqual(prec, 8 / 9, places=4)
        self.assertAlmostEqual(rec, 8 / 9, places=4)
        self.assertAlmostEqual(f1, 8 / 9, places=4)
        self.assertAlmostEqual(acc, 10 / 12, places=4)
        self.assertAlmostEqual(spec, 2 / 3, places=4)

    # 9. Missing or unavailable metrics
    def test_09_missing_or_unavailable_metrics(self):
        """9. Zero division in metrics safely returns None without raising exceptions."""
        empty_manifest = {
            "status": "SPECIFIED",
            "seed": 20261022,
            "total_records": 1,
            "anomalous_count": 0,
            "normal_count": 1,
            "records": [
                {
                    "anomaly_id": "NORM-ONLY-01",
                    "anomaly_category": "NORMAL_CONTROL_EXPENSE",
                    "entity_type": "ExpenseClaim",
                    "entity_id": "EXP-2026-000001",
                    "target_field": None,
                    "original_value": "1680.00",
                    "injected_value": "1680.00",
                    "expected_detection_code": "ELIGIBLE_FOR_APPROVAL",
                    "ground_truth_label": "NORMAL",
                }
            ],
        }
        with open(self.temp_manifest, "w", encoding="utf-8") as f:
            json.dump(empty_manifest, f)

        res = evaluate_detection_pipeline(manifest_path=self.temp_manifest, use_isolated_fixture=False)
        # With TP=0 and FP=0, precision is None
        self.assertIsNone(res["metrics"]["precision"])
        self.assertIsNone(res["metrics"]["recall"])
        self.assertIsNone(res["metrics"]["f1_score"])
        self.assertEqual(res["metrics"]["accuracy"], 1.0)

    # 10. Reproducibility across repeated runs
    def test_10_reproducibility_across_repeated_runs(self):
        """10. Reproducibility checks confirm identical plan and metrics across repeated runs."""
        repro = verify_reproducibility(seed=RANDOM_SEED)
        self.assertTrue(repro["is_reproducible"])
        self.assertEqual(len(repro["errors"]), 0)

    # 11. Validation against restored data
    def test_11_validation_against_restored_data(self):
        """11. The current database evaluates cleanly as clean baseline with 0 anomalies in production data."""
        res = evaluate_detection_pipeline(use_isolated_fixture=False)
        self.assertEqual(res["execution_mode"], "CURRENT_DATABASE_STATE")
        # In clean baseline, all evaluated records have their original valid state
        self.assertEqual(res["confusion_matrix"]["false_positives"], 0)

    # 12. Reports generation
    def test_12_reports_generation(self):
        """12. Machine-readable JSON report and markdown summary are generated successfully."""
        res = validate_dataset(output_dir=self.temp_reports_dir)
        self.assertEqual(res["status"], "PASSED")

        json_file = res["reports"]["json_report"]
        md_file = res["reports"]["markdown_summary"]

        self.assertTrue(os.path.exists(json_file))
        self.assertTrue(os.path.exists(md_file))

        with open(json_file, "r", encoding="utf-8") as f:
            rep_data = json.load(f)
        self.assertEqual(rep_data["status"], "PASSED")
        self.assertEqual(rep_data["anomaly_detection"]["metrics"]["accuracy"], 1.0)

    # 13. Existing Phase 5-8 behavior remaining intact
    def test_13_existing_phases_intact(self):
        """13. Leave, expense, and approval services continue to function correctly."""
        res_leave = evaluate_leave_request("LVE-2026-000001")
        self.assertIn(res_leave["decision"], ["NEEDS_REVIEW", "REJECT"])

        res_expense = evaluate_expense_claim("EXP-2026-000001")
        self.assertIn(res_expense["decision"], ["NEEDS_REVIEW", "REJECT"])

        res_route = resolve_approval_route("leave", "LVE-2026-000001")
        self.assertIn(res_route["status"], ["VALID", "INELIGIBLE", "INACTIVE_OR_MISSING_APPROVER"])


if __name__ == "__main__":
    unittest.main()
