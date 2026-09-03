"""
ReconcileAI - Dashboard API Client
Centralized, type-hinted HTTP client managing communication between the Streamlit
frontend and the FastAPI Phase 14 backend. Provides robust error handling,
timeout management, and clean domain helpers without any UI rendering logic.
"""

import hashlib
import hmac
import json
import logging
import os
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 10
RECONCILE_TIMEOUT_SECONDS = 45


class APIClientError(Exception):
    """Base exception for all API client failures."""
    pass


class APIConnectionError(APIClientError):
    """Raised when the client cannot connect to the FastAPI backend."""
    pass


class APITimeoutError(APIClientError):
    """Raised when an API request exceeds the configured timeout."""
    pass


class APIStatusError(APIClientError):
    """Raised when the API returns an unexpected HTTP error code."""
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ReconcileAPIClient:
    """
    HTTP client for the ReconcileAI Phase 14 REST API.
    Provides methods for health checks, data loading, reconciliation execution,
    exception resolution, transaction querying, and audit trail inspection.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        clean_path = path.lstrip("/")
        return f"{self.base_url}/{clean_path}"

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Performs a GET request with centralized error handling."""
        req_timeout = timeout or self.timeout
        url = self._url(path)
        try:
            resp = requests.get(url, params=params, timeout=req_timeout)
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout connecting to {url}: {e}")
            raise APITimeoutError(f"Request to {url} timed out after {req_timeout}s.") from e
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to {url}: {e}")
            raise APIConnectionError(
                f"Could not connect to ReconcileAI backend at {self.base_url}. Ensure FastAPI is running."
            ) from e
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error to {url}: {e}")
            raise APIClientError(f"Network error: {str(e)}") from e

        return self._handle_response(resp)

    def _post(self, path: str, json_data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Performs a POST request with centralized error handling."""
        req_timeout = timeout or self.timeout
        url = self._url(path)
        try:
            resp = requests.post(url, json=json_data, params=params, timeout=req_timeout)
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout posting to {url}: {e}")
            raise APITimeoutError(f"Request to {url} timed out after {req_timeout}s.") from e
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error posting to {url}: {e}")
            raise APIConnectionError(
                f"Could not connect to ReconcileAI backend at {self.base_url}. Ensure FastAPI is running."
            ) from e
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error posting to {url}: {e}")
            raise APIClientError(f"Network error: {str(e)}") from e

        return self._handle_response(resp)

    def _handle_response(self, resp: requests.Response) -> Dict[str, Any]:
        """Extracts JSON or raises typed APIStatusError."""
        if resp.status_code >= 400:
            detail = ""
            try:
                data = resp.json()
                detail = data.get("detail", str(data))
            except Exception:
                detail = resp.text or f"HTTP {resp.status_code} Error"
            raise APIStatusError(status_code=resp.status_code, detail=detail)

        try:
            return resp.json()
        except Exception as e:
            raise APIClientError(f"Failed to parse JSON response from server: {str(e)}") from e

    # -------------------------------------------------------------------------
    # 1. System Health & Summary Endpoints
    # -------------------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """Retrieves backend health status, database connection, and AI flag."""
        return self._get("/health", timeout=3)

    def get_summary(self) -> Dict[str, Any]:
        """
        Retrieves real-time operational summary metrics from the backend.
        Returns total transactions, match rates, value at risk, category breakdown,
        and SLA breakdown.
        """
        return self._get("/reports/summary")

    # -------------------------------------------------------------------------
    # 2. Transaction Data Endpoints
    # -------------------------------------------------------------------------

    def get_transactions(
        self,
        source: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Retrieves paginated canonical transactions with optional filtering.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if source and source != "ALL":
            params["source"] = source
        if status and status != "ALL":
            params["status"] = status
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._get("/transactions", params=params)

    def load_synthetic(self, data_dir: str = "data", is_held_out: bool = False) -> Dict[str, Any]:
        """
        Ingests synthetic datasets from disk into canonical database.
        """
        params = {"data_dir": data_dir, "is_held_out": is_held_out}
        return self._post("/transactions/load-synthetic", params=params, timeout=30)

    # -------------------------------------------------------------------------
    # 3. Reconciliation Execution & Results Endpoints
    # -------------------------------------------------------------------------

    def run_reconciliation(self) -> Dict[str, Any]:
        """
        Triggers multi-source reconciliation pipeline via backend API.
        Returns summary with total clusters, match rate, and value-at-risk.
        """
        return self._post("/reconcile", timeout=RECONCILE_TIMEOUT_SECONDS)

    def get_reconciliation_results(
        self,
        final_decision: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        reconciliation_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Queries paginated reconciliation results with optional filters.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if final_decision and final_decision != "ALL":
            params["final_decision"] = final_decision
        if is_resolved is not None:
            params["is_resolved"] = is_resolved
        if reconciliation_id:
            params["reconciliation_id"] = reconciliation_id.strip()
        return self._get("/reconciliation/results", params=params)

    # -------------------------------------------------------------------------
    # 4. Human-in-the-Loop Exception Management Endpoints
    # -------------------------------------------------------------------------

    def get_exceptions(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Queries paginated reconciliation exceptions.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status and status != "ALL":
            params["status"] = status
        if severity and severity != "ALL":
            params["severity"] = severity
        if category and category != "ALL":
            params["category"] = category
        return self._get("/exceptions", params=params)

    def get_exception(self, exception_id: str) -> Dict[str, Any]:
        """
        Retrieves detailed information for a single exception.
        """
        return self._get(f"/exceptions/{exception_id}")

    def approve_exception(
        self,
        exception_id: str,
        reviewer_id: str = "HUMAN_OPERATOR",
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authoritative human approval action for an exception.
        Resolves exception and marks linked reconciliation result as resolved.
        """
        if not reviewer_id or not reviewer_id.strip():
            raise ValueError("Reviewer ID is mandatory for human approval.")
        payload = {
            "reviewer_id": reviewer_id.strip(),
            "notes": notes or "Approved by human operator"
        }
        return self._post(f"/exceptions/{exception_id}/approve", json_data=payload)

    def reject_exception(
        self,
        exception_id: str,
        reviewer_id: str = "HUMAN_OPERATOR",
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authoritative human rejection action for an exception.
        Sets exception status to REJECTED and marks linked reconciliation result as resolved.
        """
        if not reviewer_id or not reviewer_id.strip():
            raise ValueError("Reviewer ID is mandatory for human rejection.")
        payload = {
            "reviewer_id": reviewer_id.strip(),
            "notes": notes or "Rejected by human operator"
        }
        return self._post(f"/exceptions/{exception_id}/reject", json_data=payload)

    # -------------------------------------------------------------------------
    # 5. Immutable Audit Trail Endpoints (Read-Only)
    # -------------------------------------------------------------------------

    def get_audit(
        self,
        entity: Optional[str] = None,
        entity_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Queries immutable audit trail records with composable filters.
        Strictly read-only; no mutating methods are defined or allowed.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if entity and entity != "ALL":
            params["entity"] = entity
        if entity_id:
            params["entity_id"] = entity_id.strip()
        if action and action != "ALL":
            params["action"] = action
        return self._get("/audit", params=params)

    # -------------------------------------------------------------------------
    # 6. Webhook Simulator Endpoints
    # -------------------------------------------------------------------------

    def simulate_webhook(
        self,
        payload: Dict[str, Any],
        secret: Optional[str] = None,
        tamper_signature: bool = False
    ) -> Dict[str, Any]:
        """
        Submits a gateway webhook payload to POST /webhook/payment with an HMAC SHA-256 signature.
        If tamper_signature is True, generates an intentionally invalid signature
        to demonstrate signature verification failure and auditing.
        """
        webhook_secret = secret or os.getenv("WEBHOOK_SECRET")
        if not webhook_secret:
            raise APIClientError(
                "Webhook simulation requires a configured secret. "
                "Set the WEBHOOK_SECRET environment variable or provide an explicit secret argument."
            )
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        if tamper_signature:
            signature = "invalid_signature_hex_deadbeef_0000000000000000"
        else:
            signature = hmac.new(
                webhook_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256
            ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature
        }
        url = self._url("/webhook/payment")
        try:
            resp = requests.post(url, data=raw_body, headers=headers, timeout=self.timeout)
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout connecting to {url}: {e}")
            raise APITimeoutError(f"Request to {url} timed out after {self.timeout}s.") from e
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to {url}: {e}")
            raise APIConnectionError(
                f"Could not connect to ReconcileAI backend at {self.base_url}. Ensure FastAPI is running."
            ) from e
        except requests.exceptions.RequestException as e:
            logger.error(f"Unexpected request error to {url}: {e}")
            raise APIClientError(f"Request to {url} failed: {e}") from e

        if not resp.ok:
            detail = resp.text
            try:
                err_json = resp.json()
                detail = err_json.get("detail", detail)
            except Exception:
                pass
            raise APIStatusError(status_code=resp.status_code, detail=detail)

        return resp.json()

    # -------------------------------------------------------------------------
    # 7. Benchmark Evaluation Endpoints
    # -------------------------------------------------------------------------

    def run_benchmark(
        self,
        is_held_out: bool = False,
        data_dir: str = "data"
    ) -> Dict[str, Any]:
        """
        Executes the Phase 13 reconciliation benchmark via POST /benchmark/run.
        Returns serialized classification, operational, performance, and financial metrics.
        """
        params = {"is_held_out": is_held_out, "data_dir": data_dir}
        return self._post("/benchmark/run", params=params)


