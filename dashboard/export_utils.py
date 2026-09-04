"""
ReconcileAI - Reusable In-Memory Export Utilities (Phase 16)
Provides pure in-memory serialization to CSV, Excel (.xlsx), JSON, and formatted Text/Markdown.
Uses standard library io and existing pandas/openpyxl dependencies.
Never creates temporary files on disk.
"""

import io
import json
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union
import pandas as pd


def _json_serial_default(obj: Any) -> Any:
    """Safe serializer for datetime, date, Decimal, and other non-standard JSON types."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def dataframe_to_csv_bytes(df: Optional[pd.DataFrame]) -> bytes:
    """
    Serializes a pandas DataFrame into UTF-8 encoded RFC-4180 CSV bytes using an in-memory buffer.
    Safely handles empty DataFrames and Unicode/INR symbols without file writes.
    """
    if df is None or df.empty:
        if df is not None and len(df.columns) > 0:
            return ",".join(str(c) for c in df.columns).encode("utf-8") + b"\n"
        return b""

    csv_str = df.to_csv(index=False)
    return csv_str.encode("utf-8")


def dataframe_to_excel_bytes(df: Optional[pd.DataFrame], sheet_name: str = "ReconcileAI_Data") -> bytes:
    """
    Serializes a single pandas DataFrame into a styled Excel XLSX binary stream using openpyxl.
    Operates strictly in-memory via io.BytesIO.
    """
    clean_sheet = sheet_name[:31] if sheet_name else "Data"
    buffer = io.BytesIO()

    target_df = df if df is not None else pd.DataFrame()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        target_df.to_excel(writer, sheet_name=clean_sheet, index=False)

    buffer.seek(0)
    return buffer.getvalue()


def dataframes_to_excel_bytes(sheets: Dict[str, pd.DataFrame]) -> bytes:
    """
    Serializes multiple DataFrames into a multi-tab Excel workbook binary stream using openpyxl.
    Operates strictly in-memory via io.BytesIO.
    """
    buffer = io.BytesIO()

    if not sheets:
        sheets = {"Summary": pd.DataFrame({"Status": ["No data available"]})}

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_title, df in sheets.items():
            # Excel sheet names cannot exceed 31 characters
            clean_title = sheet_title.replace("/", "_").replace("\\", "_")[:31] or "Sheet"
            target_df = df if df is not None else pd.DataFrame()
            target_df.to_excel(writer, sheet_name=clean_title, index=False)

    buffer.seek(0)
    return buffer.getvalue()


def dict_to_json_bytes(data: Any, indent: int = 2) -> bytes:
    """
    Serializes arbitrary dictionaries, lists, or Pydantic/dataclass objects into formatted JSON bytes.
    Handles dates, timestamps, decimals, and nested models.
    """
    if data is None:
        data = {}

    json_str = json.dumps(data, default=_json_serial_default, indent=indent, ensure_ascii=False)
    return json_str.encode("utf-8")


def text_to_bytes(text: str) -> bytes:
    """
    Encodes Markdown or plain text reports into UTF-8 bytes for file download.
    """
    return (text or "").encode("utf-8")
