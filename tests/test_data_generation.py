"""
Phase 2 & 3 Unit Tests: Synthetic Data Generation & Ground Truth Integrity
Verifies:
1. Existence and non-emptiness of generated CSV files
2. Exact distribution of 9 scenario types (100 records total)
3. Reproducibility using seed=42
4. Correctness of Ground Truth structure and isolation
"""

import os
import pandas as pd
import pytest
from scripts.generate_data import generate_datasets

def test_generated_csv_files_exist():
    """Ensure all primary CSV files are present in the data/ folder."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    
    expected_files = [
        "gateway_transactions.csv",
        "bank_transactions.csv",
        "erp_transactions.csv",
        "ground_truth.csv"
    ]
    for filename in expected_files:
        filepath = os.path.join(data_dir, filename)
        assert os.path.exists(filepath), f"File {filename} was not found."
        df = pd.read_csv(filepath)
        assert len(df) > 0, f"File {filename} is empty."

def test_scenario_distribution_counts():
    """Verify that the 100 benchmark records strictly adhere to the required scenario breakdown."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gt_path = os.path.join(base_dir, "data", "ground_truth.csv")
    df_gt = pd.read_csv(gt_path)
    
    assert len(df_gt) == 100, f"Expected 100 total records, got {len(df_gt)}"
    
    counts = df_gt['scenario_type'].value_counts().to_dict()
    assert counts.get("EXACT_MATCH") == 55, "Expected 55 EXACT_MATCH scenarios"
    assert counts.get("AMOUNT_MISMATCH") == 10, "Expected 10 AMOUNT_MISMATCH scenarios"
    assert counts.get("MISSING_BANK_TRANSACTION") == 8, "Expected 8 MISSING_BANK_TRANSACTION scenarios"
    assert counts.get("MISSING_GATEWAY_TRANSACTION") == 5, "Expected 5 MISSING_GATEWAY_TRANSACTION scenarios"
    assert counts.get("DUPLICATE_TRANSACTION") == 5, "Expected 5 DUPLICATE_TRANSACTION scenarios"
    assert counts.get("DATE_MISMATCH") == 5, "Expected 5 DATE_MISMATCH scenarios"
    assert counts.get("REFERENCE_MISMATCH") == 5, "Expected 5 REFERENCE_MISMATCH scenarios"
    assert counts.get("PARTIAL_PAYMENT") == 4, "Expected 4 PARTIAL_PAYMENT scenarios"
    assert counts.get("FAILED_PAYMENT") == 3, "Expected 3 FAILED_PAYMENT scenarios"

def test_ground_truth_columns():
    """Ensure ground truth table has all required audit and evaluation fields."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gt_path = os.path.join(base_dir, "data", "ground_truth.csv")
    df_gt = pd.read_csv(gt_path)
    
    required_cols = [
        "scenario_id", "scenario_type", "payment_id",
        "gateway_transaction_id", "bank_transaction_id", "erp_invoice_id",
        "order_id", "customer_id", "ground_truth_status",
        "expected_action", "anomaly_flag", "description"
    ]
    for col in required_cols:
        assert col in df_gt.columns, f"Ground truth is missing column: {col}"

def test_data_generation_reproducibility():
    """Ensure that running generate_datasets with seed=42 produces identical dataframes."""
    res1 = generate_datasets(num_records=100, seed=42, output_dir="data", is_held_out=False)
    df1 = pd.read_csv(res1["ground_truth"])
    
    res2 = generate_datasets(num_records=100, seed=42, output_dir="data", is_held_out=False)
    df2 = pd.read_csv(res2["ground_truth"])
    
    pd.testing.assert_frame_equal(df1, df2)
