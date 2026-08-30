"""
ReconcileAI - Synthetic Multi-Source Financial Data Generator
Generates realistic Payment Gateway, Bank Statement, and ERP Ledger records
with isolated ground truth for objective evaluation and benchmarking.

Seed is fixed (42) for 100% reproducibility.
"""

import os
import random
import pandas as pd
from datetime import datetime, timedelta

def generate_datasets(num_records: int = 100, seed: int = 42, output_dir: str = "data", is_held_out: bool = False):
    random.seed(seed)
    os.makedirs(output_dir, exist_ok=True)
    
    # Prefix to distinguish baseline vs held-out dataset
    prefix = "HO_" if is_held_out else ""
    
    # Scenarios distribution for 100 records
    # 55 Exact Matches
    # 10 Amount Mismatches
    # 8 Missing Bank Transactions
    # 5 Missing Gateway Transactions
    # 5 Duplicate Transactions
    # 5 Date Mismatches
    # 5 Reference Mismatches (Fuzzy match candidates)
    # 4 Partial Payments
    # 3 Failed Payments
    
    if num_records == 100:
        scenario_counts = {
            "EXACT_MATCH": 55,
            "AMOUNT_MISMATCH": 10,
            "MISSING_BANK_TRANSACTION": 8,
            "MISSING_GATEWAY_TRANSACTION": 5,
            "DUPLICATE_TRANSACTION": 5,
            "DATE_MISMATCH": 5,
            "REFERENCE_MISMATCH": 5,
            "PARTIAL_PAYMENT": 4,
            "FAILED_PAYMENT": 3,
        }
    else:
        # Scale proportionally if num_records != 100
        scenario_counts = {
            "EXACT_MATCH": int(num_records * 0.55),
            "AMOUNT_MISMATCH": int(num_records * 0.10),
            "MISSING_BANK_TRANSACTION": int(num_records * 0.08),
            "MISSING_GATEWAY_TRANSACTION": int(num_records * 0.05),
            "DUPLICATE_TRANSACTION": int(num_records * 0.05),
            "DATE_MISMATCH": int(num_records * 0.05),
            "REFERENCE_MISMATCH": int(num_records * 0.05),
            "PARTIAL_PAYMENT": int(num_records * 0.04),
            "FAILED_PAYMENT": num_records - (
                int(num_records * 0.55) + int(num_records * 0.10) + int(num_records * 0.08) +
                int(num_records * 0.05) + int(num_records * 0.05) + int(num_records * 0.05) +
                int(num_records * 0.05) + int(num_records * 0.04)
            )
        }

    gateway_records = []
    bank_records = []
    erp_records = []
    ground_truth_records = []
    
    base_date = datetime(2026, 8, 1, 10, 0, 0)
    current_idx = 1001 if not is_held_out else 5001
    bank_balance = 5000000.0  # Starting bank balance ₹50,00,000

    payment_methods = ["UPI", "CREDIT_CARD", "NET_BANKING", "DEBIT_CARD"]
    
    for scenario_type, count in scenario_counts.items():
        for _ in range(count):
            current_idx += 1
            txn_seq = current_idx
            
            pay_id = f"{prefix}pay_{txn_seq}"
            gateway_txn_id = f"{prefix}GW{txn_seq}"
            bank_txn_id = f"{prefix}BNK{txn_seq}"
            invoice_id = f"{prefix}INV{txn_seq}"
            order_id = f"{prefix}ORD{txn_seq}"
            cust_id = f"CUST_{(txn_seq % 50) + 1:03d}"
            cust_name = f"Customer {(txn_seq % 50) + 1:03d}"
            
            # Base amount between 500 and 50000 (standard Indian e-commerce / B2B SaaS range)
            base_amount = float(random.randint(5, 500) * 100)
            fee = round(base_amount * 0.02, 2)  # 2% fee
            tax = round(fee * 0.18, 2)         # 18% GST on fee
            net_amount = round(base_amount - fee - tax, 2)
            
            # Date progression
            days_offset = (txn_seq % 25)
            txn_date = base_date + timedelta(days=days_offset, hours=random.randint(0, 8), minutes=random.randint(0, 59))
            date_str_iso = txn_date.strftime("%Y-%m-%d %H:%M:%S")
            date_str_short = txn_date.strftime("%Y-%m-%d")
            bank_value_date = (txn_date + timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Initialize record templates
            gw_item = None
            bank_item = None
            erp_item = None
            
            gt_status = "MATCHED"
            expected_action = "AUTO_RECONCILE"
            anomaly_flag = False
            gt_reason = "All three sources match within tolerance."
            
            # ----------------------------------------------------
            # Scenario handling
            # ----------------------------------------------------
            if scenario_type == "EXACT_MATCH":
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": base_amount,
                    "currency": "INR",
                    "payment_method": random.choice(payment_methods),
                    "status": "captured",
                    "transaction_date": date_str_iso,
                    "captured_at": date_str_iso,
                    "fee": fee,
                    "tax": tax,
                    "net_amount": net_amount
                }
                bank_item = {
                    "bank_transaction_id": bank_txn_id,
                    "bank_reference": pay_id,
                    "transaction_date": bank_value_date,
                    "value_date": bank_value_date,
                    "description": f"Razorpay Settlement - {pay_id} / {order_id}",
                    "credit_amount": base_amount,
                    "debit_amount": 0.0,
                    "balance": bank_balance + base_amount,
                    "bank_account": "HDFC_DEMO_009988",
                    "transaction_type": "CREDIT"
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "PAID",
                    "reference_id": pay_id
                }
                gt_status = "MATCHED"
                expected_action = "AUTO_RECONCILE"

            elif scenario_type == "AMOUNT_MISMATCH":
                # ERP and PG amount is base_amount, but bank received different amount (or vice versa)
                mismatch_diff = random.choice([50.0, -100.0, 25.50, 500.0])
                bank_amount = base_amount + mismatch_diff
                
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": base_amount,
                    "currency": "INR",
                    "payment_method": random.choice(payment_methods),
                    "status": "captured",
                    "transaction_date": date_str_iso,
                    "captured_at": date_str_iso,
                    "fee": fee,
                    "tax": tax,
                    "net_amount": net_amount
                }
                bank_item = {
                    "bank_transaction_id": bank_txn_id,
                    "bank_reference": pay_id,
                    "transaction_date": bank_value_date,
                    "value_date": bank_value_date,
                    "description": f"Settlement Discrepancy - {pay_id}",
                    "credit_amount": bank_amount,
                    "debit_amount": 0.0,
                    "balance": bank_balance + bank_amount,
                    "bank_account": "HDFC_DEMO_009988",
                    "transaction_type": "CREDIT"
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "PENDING_RECONCILIATION",
                    "reference_id": pay_id
                }
                gt_status = "AMOUNT_MISMATCH"
                expected_action = "HUMAN_REVIEW"
                anomaly_flag = True
                gt_reason = f"Gateway amount ₹{base_amount} differs from Bank credit ₹{bank_amount}."

            elif scenario_type == "MISSING_BANK_TRANSACTION":
                # PG and ERP exist, but Bank transaction has not arrived/settled
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": base_amount,
                    "currency": "INR",
                    "payment_method": random.choice(payment_methods),
                    "status": "captured",
                    "transaction_date": date_str_iso,
                    "captured_at": date_str_iso,
                    "fee": fee,
                    "tax": tax,
                    "net_amount": net_amount
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "AWAITING_BANK_CREDIT",
                    "reference_id": pay_id
                }
                bank_item = None
                gt_status = "MISSING_BANK_TRANSACTION"
                expected_action = "HUMAN_REVIEW"
                gt_reason = "Payment captured on Gateway and recorded in ERP, but missing from Bank statement."

            elif scenario_type == "MISSING_GATEWAY_TRANSACTION":
                # Direct bank transfer (NEFT/RTGS) recorded in bank and ERP, but no PG record
                bank_item = {
                    "bank_transaction_id": bank_txn_id,
                    "bank_reference": f"NEFT_{txn_seq}",
                    "transaction_date": bank_value_date,
                    "value_date": bank_value_date,
                    "description": f"Direct NEFT Credit from {cust_name} / {invoice_id}",
                    "credit_amount": base_amount,
                    "debit_amount": 0.0,
                    "balance": bank_balance + base_amount,
                    "bank_account": "HDFC_DEMO_009988",
                    "transaction_type": "CREDIT"
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "MANUAL_BANK_TRANSFER",
                    "reference_id": f"NEFT_{txn_seq}"
                }
                gw_item = None
                gt_status = "UNEXPECTED_BANK_TRANSACTION"
                expected_action = "AI_REVIEW"
                gt_reason = "Direct bank transfer present in Bank and ERP without Gateway transaction."

            elif scenario_type == "DUPLICATE_TRANSACTION":
                # Gateway processed two charges for same order/reference
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": base_amount,
                    "currency": "INR",
                    "payment_method": "UPI",
                    "status": "captured",
                    "transaction_date": date_str_iso,
                    "captured_at": date_str_iso,
                    "fee": fee,
                    "tax": tax,
                    "net_amount": net_amount
                }
                # Duplicate record with slight time delta
                duplicate_gw = dict(gw_item)
                duplicate_gw["gateway_transaction_id"] = f"{gateway_txn_id}_DUP"
                gateway_records.append(duplicate_gw)
                
                bank_item = {
                    "bank_transaction_id": bank_txn_id,
                    "bank_reference": pay_id,
                    "transaction_date": bank_value_date,
                    "value_date": bank_value_date,
                    "description": f"Duplicate Charge Detected - {pay_id}",
                    "credit_amount": base_amount,
                    "debit_amount": 0.0,
                    "balance": bank_balance + base_amount,
                    "bank_account": "HDFC_DEMO_009988",
                    "transaction_type": "CREDIT"
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "PAID",
                    "reference_id": pay_id
                }
                gt_status = "DUPLICATE_TRANSACTION"
                expected_action = "HUMAN_REVIEW"
                anomaly_flag = True
                gt_reason = "Duplicate gateway transaction detected for the same order reference."

            elif scenario_type == "DATE_MISMATCH":
                # Bank settlement delayed by 18 days (abnormal settlement cycle)
                abnormal_bank_date = (txn_date + timedelta(days=18)).strftime("%Y-%m-%d")
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": base_amount,
                    "currency": "INR",
                    "payment_method": random.choice(payment_methods),
                    "status": "captured",
                    "transaction_date": date_str_iso,
                    "captured_at": date_str_iso,
                    "fee": fee,
                    "tax": tax,
                    "net_amount": net_amount
                }
                bank_item = {
                    "bank_transaction_id": bank_txn_id,
                    "bank_reference": pay_id,
                    "transaction_date": abnormal_bank_date,
                    "value_date": abnormal_bank_date,
                    "description": f"Delayed Settlement - {pay_id}",
                    "credit_amount": base_amount,
                    "debit_amount": 0.0,
                    "balance": bank_balance + base_amount,
                    "bank_account": "HDFC_DEMO_009988",
                    "transaction_type": "CREDIT"
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "PAID",
                    "reference_id": pay_id
                }
                gt_status = "DATE_MISMATCH"
                expected_action = "AI_REVIEW"
                gt_reason = "Date gap between gateway capture and bank value date exceeds 3-day tolerance."

            elif scenario_type == "REFERENCE_MISMATCH":
                # Typo or mutated reference in bank statement: e.g. pay_1045 vs pay_104S or PAY_I045
                altered_ref = pay_id[:-1] + ("A" if pay_id[-1] != "A" else "B")
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": base_amount,
                    "currency": "INR",
                    "payment_method": random.choice(payment_methods),
                    "status": "captured",
                    "transaction_date": date_str_iso,
                    "captured_at": date_str_iso,
                    "fee": fee,
                    "tax": tax,
                    "net_amount": net_amount
                }
                bank_item = {
                    "bank_transaction_id": bank_txn_id,
                    "bank_reference": altered_ref,
                    "transaction_date": bank_value_date,
                    "value_date": bank_value_date,
                    "description": f"Razorpay Settlement Ref: {altered_ref} Order {order_id}",
                    "credit_amount": base_amount,
                    "debit_amount": 0.0,
                    "balance": bank_balance + base_amount,
                    "bank_account": "HDFC_DEMO_009988",
                    "transaction_type": "CREDIT"
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "PAID",
                    "reference_id": pay_id
                }
                gt_status = "REFERENCE_MISMATCH"
                expected_action = "AI_REVIEW"
                gt_reason = f"Bank reference '{altered_ref}' slightly mutated from gateway reference '{pay_id}'."

            elif scenario_type == "PARTIAL_PAYMENT":
                # Customer paid 60% of the invoice
                partial_amount = round(base_amount * 0.60, 2)
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": partial_amount,
                    "currency": "INR",
                    "payment_method": random.choice(payment_methods),
                    "status": "captured",
                    "transaction_date": date_str_iso,
                    "captured_at": date_str_iso,
                    "fee": round(partial_amount * 0.02, 2),
                    "tax": round(partial_amount * 0.02 * 0.18, 2),
                    "net_amount": round(partial_amount - (partial_amount * 0.02 * 1.18), 2)
                }
                bank_item = {
                    "bank_transaction_id": bank_txn_id,
                    "bank_reference": pay_id,
                    "transaction_date": bank_value_date,
                    "value_date": bank_value_date,
                    "description": f"Partial Settlement - {pay_id}",
                    "credit_amount": partial_amount,
                    "debit_amount": 0.0,
                    "balance": bank_balance + partial_amount,
                    "bank_account": "HDFC_DEMO_009988",
                    "transaction_type": "CREDIT"
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,  # Full invoice
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "PARTIALLY_PAID",
                    "reference_id": pay_id
                }
                gt_status = "PARTIAL_PAYMENT"
                expected_action = "HUMAN_REVIEW"
                gt_reason = f"Received amount ₹{partial_amount} is less than invoice expected amount ₹{base_amount}."

            elif scenario_type == "FAILED_PAYMENT":
                gw_item = {
                    "gateway_transaction_id": gateway_txn_id,
                    "payment_id": pay_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "amount": base_amount,
                    "currency": "INR",
                    "payment_method": random.choice(payment_methods),
                    "status": "failed",
                    "transaction_date": date_str_iso,
                    "captured_at": None,
                    "fee": 0.0,
                    "tax": 0.0,
                    "net_amount": 0.0
                }
                erp_item = {
                    "invoice_id": invoice_id,
                    "order_id": order_id,
                    "customer_id": cust_id,
                    "customer_name": cust_name,
                    "invoice_amount": base_amount,
                    "expected_payment": base_amount,
                    "invoice_date": date_str_short,
                    "payment_status": "UNPAID",
                    "reference_id": pay_id
                }
                bank_item = None
                gt_status = "FAILED_PAYMENT"
                expected_action = "AUTO_RECONCILE"  # Reconciled as confirmed failed/unsettled
                gt_reason = "Payment failed at gateway; no bank settlement expected."

            # Append records
            if gw_item:
                gateway_records.append(gw_item)
            if bank_item:
                bank_records.append(bank_item)
            if erp_item:
                erp_records.append(erp_item)

            ground_truth_records.append({
                "scenario_id": f"SCN_{txn_seq}",
                "scenario_type": scenario_type,
                "payment_id": pay_id,
                "gateway_transaction_id": gateway_txn_id if gw_item else "NONE",
                "bank_transaction_id": bank_txn_id if bank_item else "NONE",
                "erp_invoice_id": invoice_id if erp_item else "NONE",
                "order_id": order_id,
                "customer_id": cust_id,
                "ground_truth_status": gt_status,
                "expected_action": expected_action,
                "anomaly_flag": anomaly_flag,
                "description": gt_reason
            })

    # Convert to DataFrames
    df_gw = pd.DataFrame(gateway_records)
    df_bank = pd.DataFrame(bank_records)
    df_erp = pd.DataFrame(erp_records)
    df_gt = pd.DataFrame(ground_truth_records)

    # Save CSVs
    file_prefix = "held_out_" if is_held_out else ""
    gw_path = os.path.join(output_dir, f"{file_prefix}gateway_transactions.csv")
    bank_path = os.path.join(output_dir, f"{file_prefix}bank_transactions.csv")
    erp_path = os.path.join(output_dir, f"{file_prefix}erp_transactions.csv")
    gt_path = os.path.join(output_dir, f"{file_prefix}ground_truth.csv")

    df_gw.to_csv(gw_path, index=False)
    df_bank.to_csv(bank_path, index=False)
    df_erp.to_csv(erp_path, index=False)
    df_gt.to_csv(gt_path, index=False)

    print(f"[Data Generator] Successfully generated {'held-out test' if is_held_out else 'primary'} dataset:")
    print(f"  - Gateway Transactions: {len(df_gw)} -> {gw_path}")
    print(f"  - Bank Transactions:    {len(df_bank)} -> {bank_path}")
    print(f"  - ERP Invoices:         {len(df_erp)} -> {erp_path}")
    print(f"  - Ground Truth Records: {len(df_gt)} -> {gt_path}")
    print(f"  - Scenario Distribution:\n{df_gt['scenario_type'].value_counts().to_string()}\n")
    
    return {
        "gateway": gw_path,
        "bank": bank_path,
        "erp": erp_path,
        "ground_truth": gt_path,
        "counts": df_gt['scenario_type'].value_counts().to_dict()
    }

if __name__ == "__main__":
    # Generate primary 100-scenario dataset
    print("=== Generating Primary 100 Scenarios (Seed 42) ===")
    generate_datasets(num_records=100, seed=42, output_dir="data", is_held_out=False)
    
    # Generate held-out 100-scenario dataset for generalization evaluation
    print("=== Generating Held-Out 100 Test Scenarios (Seed 999) ===")
    generate_datasets(num_records=100, seed=999, output_dir="data", is_held_out=True)
