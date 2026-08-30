"""
ReconcileAI - Ingestion Service
Loads raw financial datasets (CSVs or dictionaries), passes them through the DataNormalizer,
and stores canonical records in the database.
"""

import json
import pandas as pd
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from backend.models.transaction import Transaction
from backend.schemas.transaction import CanonicalTransaction
from backend.services.normalizer import DataNormalizer

class IngestionService:
    """Handles multi-source financial ingestion and database persistence."""

    @staticmethod
    def ingest_records(
        db: Session,
        records: List[Dict[str, Any]],
        source: str
    ) -> List[Transaction]:
        """
        Normalizes a batch of raw records from a given source (GATEWAY, BANK, ERP)
        and persists them into the transactions table.
        """
        canonical_list: List[CanonicalTransaction] = DataNormalizer.normalize_batch(records, source)
        db_transactions: List[Transaction] = []

        for item in canonical_list:
            # Check if record already exists in database (prevent exact duplicate ingestion)
            existing = db.query(Transaction).filter_by(
                transaction_id=item.transaction_id,
                source=item.source
            ).first()

            if existing:
                # Update existing record fields
                existing.reference_id = item.reference_id
                existing.order_id = item.order_id
                existing.customer_id = item.customer_id
                existing.amount = item.amount
                existing.currency = item.currency
                existing.transaction_date = item.transaction_date
                existing.status = item.status
                existing.transaction_type = item.transaction_type
                existing.description = item.description
                existing.metadata_json = json.dumps(item.metadata)
                db_transactions.append(existing)
            else:
                # Create new Transaction ORM model
                new_txn = Transaction(
                    transaction_id=item.transaction_id,
                    source=item.source,
                    reference_id=item.reference_id,
                    order_id=item.order_id,
                    customer_id=item.customer_id,
                    amount=item.amount,
                    currency=item.currency,
                    transaction_date=item.transaction_date,
                    status=item.status,
                    transaction_type=item.transaction_type,
                    description=item.description,
                    metadata_json=json.dumps(item.metadata)
                )
                db.add(new_txn)
                db_transactions.append(new_txn)

        db.commit()
        for txn in db_transactions:
            db.refresh(txn)

        return db_transactions

    @classmethod
    def ingest_csv_file(cls, db: Session, csv_path: str, source: str) -> List[Transaction]:
        """Reads a CSV file from disk and ingests its records."""
        df = pd.read_csv(csv_path)
        records = df.to_dict(orient="records")
        return cls.ingest_records(db, records, source)
