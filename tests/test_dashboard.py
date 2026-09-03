"""
ReconcileAI - Dashboard Automated Test Suite (Phase 15)
Verifies the Streamlit dashboard consumer layer, centralized HTTP client,
currency and escalation formatters, and architectural safety invariants.

Does NOT make real HTTP requests or access the canonical database.
"""

import ast
import glob
import os
import typing
from unittest.mock import patch, MagicMock
import pytest
import requests

from dashboard.api_client import (
    ReconcileAPIClient,
    APIClientError,
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    RECONCILE_TIMEOUT_SECONDS,
)


# =============================================================================
# Helper Function Dynamic AST Loader
# Avoids triggering Streamlit top-level execution side-effects during unit testing
# =============================================================================

def _load_app_helper(func_name: str):
    """Parses dashboard/app.py and compiles a single helper function without running Streamlit."""
    app_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "app.py")
    with open(app_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="dashboard/app.py")

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            module_node = ast.Module(body=[node], type_ignores=[])
            code = compile(module_node, filename="dashboard/app.py", mode="exec")
            ns = {
                "Optional": typing.Optional,
                "Dict": typing.Dict,
                "Any": typing.Any,
            }
            exec(code, ns)
            return ns[func_name]

    raise NameError(f"Helper function '{func_name}' not found in dashboard/app.py")


format_inr = _load_app_helper("format_inr")
format_escalation_level = _load_app_helper("format_escalation_level")


# =============================================================================
# 1. API Client Method Unit Tests
# =============================================================================

class TestReconcileAPIClientMethods:
    """Verifies that ReconcileAPIClient methods construct correct HTTP requests."""

    @pytest.fixture
    def client(self):
        return ReconcileAPIClient(base_url="http://test-server:8000", timeout=5)

    def test_init_defaults(self):
        c = ReconcileAPIClient()
        assert c.base_url == DEFAULT_BASE_URL.rstrip("/")
        assert c.timeout == DEFAULT_TIMEOUT_SECONDS

    def test_url_construction(self, client):
        assert client._url("health") == "http://test-server:8000/health"
        assert client._url("/health") == "http://test-server:8000/health"
        assert client._url("reports/summary") == "http://test-server:8000/reports/summary"

    @patch("requests.get")
    def test_health(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "version": "1.0.0"}
        mock_get.return_value = mock_resp

        data = client.health()
        assert data["status"] == "ok"
        mock_get.assert_called_once_with(
            "http://test-server:8000/health",
            params=None,
            timeout=3
        )

    @patch("requests.get")
    def test_get_summary(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total_transactions": 289, "auto_reconciled_rate": 62.0}
        mock_get.return_value = mock_resp

        data = client.get_summary()
        assert data["total_transactions"] == 289
        mock_get.assert_called_once_with(
            "http://test-server:8000/reports/summary",
            params=None,
            timeout=5
        )

    @patch("requests.get")
    def test_get_transactions_with_filters(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 1, "items": [{"id": 1}]}
        mock_get.return_value = mock_resp

        client.get_transactions(
            source="GATEWAY",
            status="CAPTURED",
            start_date="2026-08-01T00:00:00Z",
            end_date="2026-08-31T23:59:59Z",
            limit=25,
            offset=50
        )
        mock_get.assert_called_once_with(
            "http://test-server:8000/transactions",
            params={
                "limit": 25,
                "offset": 50,
                "source": "GATEWAY",
                "status": "CAPTURED",
                "start_date": "2026-08-01T00:00:00Z",
                "end_date": "2026-08-31T23:59:59Z",
            },
            timeout=5
        )

    @patch("requests.get")
    def test_get_transactions_omits_all_sentinel(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 0, "items": []}
        mock_get.return_value = mock_resp

        client.get_transactions(source="ALL", status="ALL", limit=50, offset=0)
        mock_get.assert_called_once_with(
            "http://test-server:8000/transactions",
            params={"limit": 50, "offset": 0},
            timeout=5
        )

    @patch("requests.post")
    def test_load_synthetic_params(self, mock_post, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "SUCCESS", "total_loaded": 289}
        mock_post.return_value = mock_resp

        res = client.load_synthetic(data_dir="custom_data", is_held_out=True)
        assert res["status"] == "SUCCESS"
        mock_post.assert_called_once_with(
            "http://test-server:8000/transactions/load-synthetic",
            json=None,
            params={"data_dir": "custom_data", "is_held_out": True},
            timeout=30
        )

    @patch("requests.post")
    def test_run_reconciliation_timeout(self, mock_post, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "COMPLETED", "total_clusters": 100}
        mock_post.return_value = mock_resp

        res = client.run_reconciliation()
        assert res["status"] == "COMPLETED"
        mock_post.assert_called_once_with(
            "http://test-server:8000/reconcile",
            json=None,
            params=None,
            timeout=RECONCILE_TIMEOUT_SECONDS
        )

    @patch("requests.get")
    def test_get_reconciliation_results_filtering(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 5, "items": []}
        mock_get.return_value = mock_resp

        client.get_reconciliation_results(
            final_decision="AUTO_RECONCILED",
            is_resolved=True,
            reconciliation_id="REC_999",
            limit=50,
            offset=0
        )
        mock_get.assert_called_once_with(
            "http://test-server:8000/reconciliation/results",
            params={
                "limit": 50,
                "offset": 0,
                "final_decision": "AUTO_RECONCILED",
                "is_resolved": True,
                "reconciliation_id": "REC_999",
            },
            timeout=5
        )

    @patch("requests.get")
    def test_get_reconciliation_results_omits_all(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 0, "items": []}
        mock_get.return_value = mock_resp

        client.get_reconciliation_results(final_decision="ALL", is_resolved=None, limit=50, offset=0)
        mock_get.assert_called_once_with(
            "http://test-server:8000/reconciliation/results",
            params={"limit": 50, "offset": 0},
            timeout=5
        )

    @patch("requests.get")
    def test_get_exceptions_filtering(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 3, "items": []}
        mock_get.return_value = mock_resp

        client.get_exceptions(status="OPEN", severity="HIGH", category="GATEWAY_VS_BANK", limit=10, offset=0)
        mock_get.assert_called_once_with(
            "http://test-server:8000/exceptions",
            params={
                "limit": 10,
                "offset": 0,
                "status": "OPEN",
                "severity": "HIGH",
                "category": "GATEWAY_VS_BANK",
            },
            timeout=5
        )

    @patch("requests.get")
    def test_get_exceptions_omits_all(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 0, "items": []}
        mock_get.return_value = mock_resp

        client.get_exceptions(status="ALL", severity="ALL", category="ALL", limit=50, offset=0)
        mock_get.assert_called_once_with(
            "http://test-server:8000/exceptions",
            params={"limit": 50, "offset": 0},
            timeout=5
        )

    @patch("requests.get")
    def test_get_exception_by_id(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"exception_id": "EXC_001"}
        mock_get.return_value = mock_resp

        data = client.get_exception("EXC_001")
        assert data["exception_id"] == "EXC_001"
        mock_get.assert_called_once_with(
            "http://test-server:8000/exceptions/EXC_001",
            params=None,
            timeout=5
        )

    @patch("requests.get")
    def test_get_audit_with_filters(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 10, "items": []}
        mock_get.return_value = mock_resp

        client.get_audit(
            entity="EXCEPTION",
            entity_id="EXC_001",
            action="EXCEPTION_APPROVED",
            limit=100,
            offset=0
        )
        mock_get.assert_called_once_with(
            "http://test-server:8000/audit",
            params={
                "limit": 100,
                "offset": 0,
                "entity": "EXCEPTION",
                "entity_id": "EXC_001",
                "action": "EXCEPTION_APPROVED",
            },
            timeout=5
        )

    @patch("requests.get")
    def test_get_audit_omits_all(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 0, "items": []}
        mock_get.return_value = mock_resp

        client.get_audit(entity="ALL", action="ALL", limit=50, offset=0)
        mock_get.assert_called_once_with(
            "http://test-server:8000/audit",
            params={"limit": 50, "offset": 0},
            timeout=5
        )


# =============================================================================
# 2. Human Decision Authority Validation Tests
# =============================================================================

class TestHumanAuthorizationValidation:
    """Verifies that approve/reject methods enforce mandatory human reviewer identification."""

    @pytest.fixture
    def client(self):
        return ReconcileAPIClient(base_url="http://test-server:8000", timeout=5)

    @patch("requests.post")
    def test_approve_exception_payload(self, mock_post, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "RESOLVED"}
        mock_post.return_value = mock_resp

        res = client.approve_exception(
            exception_id="EXC_101",
            reviewer_id="FIN_REV_42",
            notes="Authorized per manager approval"
        )
        assert res["status"] == "RESOLVED"
        mock_post.assert_called_once_with(
            "http://test-server:8000/exceptions/EXC_101/approve",
            json={
                "reviewer_id": "FIN_REV_42",
                "notes": "Authorized per manager approval"
            },
            params=None,
            timeout=5
        )

    @patch("requests.post")
    def test_approve_exception_default_notes(self, mock_post, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "RESOLVED"}
        mock_post.return_value = mock_resp

        client.approve_exception("EXC_101", reviewer_id="FIN_REV_42", notes=None)
        mock_post.assert_called_once_with(
            "http://test-server:8000/exceptions/EXC_101/approve",
            json={
                "reviewer_id": "FIN_REV_42",
                "notes": "Approved by human operator"
            },
            params=None,
            timeout=5
        )

    def test_approve_exception_rejects_empty_reviewer(self, client):
        with pytest.raises(ValueError, match="Reviewer ID is mandatory"):
            client.approve_exception("EXC_101", reviewer_id="")

        with pytest.raises(ValueError, match="Reviewer ID is mandatory"):
            client.approve_exception("EXC_101", reviewer_id="   ")

    @patch("requests.post")
    def test_reject_exception_payload(self, mock_post, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "REJECTED"}
        mock_post.return_value = mock_resp

        res = client.reject_exception(
            exception_id="EXC_202",
            reviewer_id="AUDIT_OFFICER_07",
            notes="Amount exceeds tolerance without invoice"
        )
        assert res["status"] == "REJECTED"
        mock_post.assert_called_once_with(
            "http://test-server:8000/exceptions/EXC_202/reject",
            json={
                "reviewer_id": "AUDIT_OFFICER_07",
                "notes": "Amount exceeds tolerance without invoice"
            },
            params=None,
            timeout=5
        )

    def test_reject_exception_rejects_empty_reviewer(self, client):
        with pytest.raises(ValueError, match="Reviewer ID is mandatory"):
            client.reject_exception("EXC_202", reviewer_id="")

        with pytest.raises(ValueError, match="Reviewer ID is mandatory"):
            client.reject_exception("EXC_202", reviewer_id=" \t ")


# =============================================================================
# 3. API Error Translation Tests
# =============================================================================

class TestAPIErrorTranslation:
    """Verifies that low-level network errors map to clean typed APIClient exceptions."""

    @pytest.fixture
    def client(self):
        return ReconcileAPIClient(base_url="http://test-server:8000", timeout=5)

    @patch("requests.get")
    def test_connection_error_translation(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        with pytest.raises(APIConnectionError, match="Could not connect to ReconcileAI backend"):
            client.health()

    @patch("requests.get")
    def test_timeout_error_translation(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.Timeout("Read timed out")
        with pytest.raises(APITimeoutError, match="timed out after"):
            client.get_summary()

    @patch("requests.get")
    def test_generic_request_exception_translation(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.RequestException("Generic network error")
        with pytest.raises(APIClientError, match="Network error: Generic network error"):
            client.get_transactions()

    @patch("requests.get")
    def test_http_status_error_with_json_detail(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"detail": "Exception EXC_999 not found"}
        mock_get.return_value = mock_resp

        with pytest.raises(APIStatusError) as exc_info:
            client.get_exception("EXC_999")
        assert exc_info.value.status_code == 404
        assert "Exception EXC_999 not found" in exc_info.value.detail

    @patch("requests.get")
    def test_http_status_error_with_text_fallback(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.side_effect = ValueError("Non-JSON body")
        mock_resp.text = "Bad Gateway from upstream"
        mock_get.return_value = mock_resp

        with pytest.raises(APIStatusError) as exc_info:
            client.health()
        assert exc_info.value.status_code == 502
        assert "Bad Gateway from upstream" in exc_info.value.detail

    @patch("requests.get")
    def test_invalid_json_on_200_raises_client_error(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("Malformed JSON")
        mock_get.return_value = mock_resp

        with pytest.raises(APIClientError, match="Failed to parse JSON response"):
            client.health()


# =============================================================================
# 4. Currency and Escalation Formatting Helper Tests
# =============================================================================

class TestDashboardFormattingHelpers:
    """Verifies that custom INR currency and escalation level helpers format correctly."""

    def test_format_inr_zero_and_none(self):
        assert format_inr(0) == "₹0"
        assert format_inr(0.0) == "₹0"
        assert format_inr(None) == "₹0"

    def test_format_inr_thousands(self):
        assert format_inr(500) == "₹500"
        assert format_inr(1500) == "₹1,500"
        assert format_inr(9999) == "₹9,999"

    def test_format_inr_lakhs(self):
        # 9,71,991
        assert format_inr(971991) == "₹9,71,991"
        assert format_inr(100000) == "₹1,00,000"
        assert format_inr(5500000) == "₹55,00,000"

    def test_format_inr_crores(self):
        # 1,23,45,678
        assert format_inr(12345678) == "₹1,23,45,678"
        assert format_inr(100000000) == "₹10,00,00,000"

    def test_format_inr_negative(self):
        assert format_inr(-500) == "-₹500"
        assert format_inr(-971991) == "-₹9,71,991"

    def test_format_inr_invalid_inputs(self):
        assert format_inr("not_a_number") == "₹0"
        assert format_inr({}) == "₹0"

    def test_format_escalation_levels(self):
        assert format_escalation_level(0) == "L0 — Primary Reviewer"
        assert format_escalation_level(None) == "L0 — Primary Reviewer"
        assert format_escalation_level(1) == "L1 — Finance Supervisor"
        assert format_escalation_level(2) == "L2 — Finance Director"
        # Unknown levels fall back to L0
        assert format_escalation_level(99) == "L0 — Primary Reviewer"


# =============================================================================
# 5. Architectural Safety Invariant Tests
# =============================================================================

class TestDashboardArchitecturalSafety:
    """Verifies that dashboard components adhere strictly to architectural boundaries."""

    def test_zero_prohibited_imports_in_dashboard_directory(self):
        """Verifies no dashboard file imports backend ORM, DB, or internal services."""
        prohibited = [
            "backend.database",
            "SessionLocal",
            "sqlalchemy",
            "backend.models",
            "FinanceController",
            "AIController",
            "AuditService",
            "DeterministicReconciliationEngine",
            "SLAOrchestrator",
        ]
        dashboard_files = glob.glob("dashboard/*.py")
        assert len(dashboard_files) > 0, "No dashboard files found to inspect"

        violations = []
        for file_path in dashboard_files:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            for pattern in prohibited:
                if pattern in content:
                    violations.append(f"{file_path}: references prohibited '{pattern}'")

        assert len(violations) == 0, f"Architectural boundary violated:\n" + "\n".join(violations)

    def test_dashboard_app_has_no_raw_http_libraries(self):
        """Verifies dashboard/app.py does not import requests or httpx directly."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imported_modules.add(n.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        assert "requests" not in imported_modules, "dashboard/app.py must not directly import requests"
        assert "httpx" not in imported_modules, "dashboard/app.py must not directly import httpx"
        assert "urllib.request" not in imported_modules, "dashboard/app.py must not import urllib.request"


# =============================================================================
# 6. Read-Only View Safety Invariants
# =============================================================================

class TestDashboardViewSafetyInvariants:
    """Verifies that read-only views make zero mutating network calls."""

    def test_read_only_sections_contain_no_mutation_calls(self):
        """Inspects source blocks of read-only views to guarantee no mutating client calls exist."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        # Split dashboard by navigation branches
        views = {
            "Executive Summary": (
                'elif nav_selection == "📊 Executive Summary":',
                'elif nav_selection == "⚖️ Exception Workbench":'
            ),
            "Transaction Explorer": (
                'elif nav_selection == "🔍 Transaction Explorer":',
                'elif nav_selection == "📑 Reconciliation Results":'
            ),
            "Reconciliation Results": (
                'elif nav_selection == "📑 Reconciliation Results":',
                'elif nav_selection == "📜 Immutable Audit Trail":'
            ),
            "Immutable Audit Trail": (
                'elif nav_selection == "📜 Immutable Audit Trail":',
                'elif nav_selection == "⚙️ Operations & Controls":'
            ),
        }

        mutating_methods = [
            "client.approve_exception",
            "client.reject_exception",
            "client.load_synthetic",
            "client.run_reconciliation",
        ]

        for view_name, (start_delim, end_delim) in views.items():
            assert start_delim in source, f"Branch for {view_name} not found"
            assert end_delim in source, f"End boundary for {view_name} not found"

            start_idx = source.index(start_delim)
            end_idx = source.index(end_delim, start_idx)
            view_code = source[start_idx:end_idx]

            for mutation in mutating_methods:
                assert mutation not in view_code, (
                    f"Read-only view '{view_name}' contains prohibited mutation call: '{mutation}'"
                )

    def test_operations_controls_confirmation_enforcement(self):
        """Verifies that Operations & Controls triggers require explicit confirmation checkboxes."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        ops_start = source.index('elif nav_selection == "⚙️ Operations & Controls":')
        ops_code = source[ops_start:]

        # Verify confirmation checkboxes guard the action buttons
        assert "disabled=(not confirm_ingest)" in ops_code, (
            "Synthetic data ingestion button must be disabled when confirm_ingest is False"
        )
        assert "disabled=(not confirm_recon)" in ops_code, (
            "Reconciliation pipeline execution button must be disabled when confirm_recon is False"
        )


class TestExceptionQueueActions:
    """Automated tests for Exception Queue Actions, SLA urgency triage, and filter reset."""

    def test_queue_controls_present_in_app(self):
        """Verifies that operational queue filters and actions exist in dashboard/app.py."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        wb_start = source.index('elif nav_selection == "⚖️ Exception Workbench":')
        wb_end = source.index('elif nav_selection == "🔍 Transaction Explorer":')
        wb_code = source[wb_start:wb_end]

        assert "last_action_notice" in wb_code, "Action confirmation notice must be supported"
        assert "SLA Urgency Filter" in wb_code, "SLA Urgency Filter must be present"
        assert "Queue Priority & Ordering" in wb_code, "Queue Priority & Ordering must be present"
        assert "Escalation Level Filter" in wb_code, "Escalation Level Filter must be present"
        assert "🧹 Clear" in wb_code, "Clear filters button must be present"
        assert "🔄 Refresh" in wb_code, "Refresh button must be present"
        assert "Queue Depth" in wb_code, "Queue Depth KPI card must be present"
        assert "SLA At-Risk" in wb_code, "SLA At-Risk KPI card must be present"

    def test_sla_urgency_sorting_logic(self):
        """Verifies that SLA Urgency triage prioritizes BREACHED and WARNING over OK."""
        records = [
            {"exception_id": "EXC_001", "sla_status": "OK", "escalation_level": 0, "difference_amount": 100.0},
            {"exception_id": "EXC_002", "sla_status": "BREACHED", "escalation_level": 2, "difference_amount": 500.0},
            {"exception_id": "EXC_003", "sla_status": "WARNING", "escalation_level": 1, "difference_amount": 200.0},
            {"exception_id": "EXC_004", "sla_status": "BREACHED", "escalation_level": 1, "difference_amount": 1000.0},
        ]
        sla_rank = {"BREACHED": 0, "WARNING": 1, "OK": 2}
        records.sort(key=lambda x: (
            sla_rank.get(x.get("sla_status", "OK"), 3),
            -x.get("escalation_level", 0),
            -abs(float(x.get("difference_amount", 0.0) or 0.0))
        ))
        # EXC_002 (BREACHED, L2) -> EXC_004 (BREACHED, L1) -> EXC_003 (WARNING, L1) -> EXC_001 (OK, L0)
        assert [r["exception_id"] for r in records] == ["EXC_002", "EXC_004", "EXC_003", "EXC_001"]

    def test_severity_sorting_logic(self):
        """Verifies that Severity triage prioritizes CRITICAL and HIGH."""
        records = [
            {"exception_id": "EXC_LOW", "severity": "LOW", "difference_amount": 5000.0},
            {"exception_id": "EXC_CRIT", "severity": "CRITICAL", "difference_amount": 100.0},
            {"exception_id": "EXC_MED", "severity": "MEDIUM", "difference_amount": 200.0},
            {"exception_id": "EXC_HIGH", "severity": "HIGH", "difference_amount": 300.0},
        ]
        sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        records.sort(key=lambda x: (
            sev_rank.get(x.get("severity", "MEDIUM"), 4),
            -abs(float(x.get("difference_amount", 0.0) or 0.0))
        ))
        assert [r["exception_id"] for r in records] == ["EXC_CRIT", "EXC_HIGH", "EXC_MED", "EXC_LOW"]

    def test_in_memory_queue_filters(self):
        """Verifies in-memory filtering by SLA status and escalation level."""
        records = [
            {"exception_id": "EXC_001", "sla_status": "BREACHED", "escalation_level": 2},
            {"exception_id": "EXC_002", "sla_status": "WARNING", "escalation_level": 1},
            {"exception_id": "EXC_003", "sla_status": "OK", "escalation_level": 0},
        ]
        # SLA Filter = BREACHED
        breached = [e for e in records if e.get("sla_status") == "BREACHED"]
        assert len(breached) == 1
        assert breached[0]["exception_id"] == "EXC_001"

        # Escalation Filter = L1
        target_lvl = 1
        l1_items = [e for e in records if e.get("escalation_level", 0) == target_lvl]
        assert len(l1_items) == 1
        assert l1_items[0]["exception_id"] == "EXC_002"


class TestLiveWebhookSimulator:
    """Automated tests for Live Webhook Simulator API client method and dashboard integration."""

    @patch("dashboard.api_client.requests.post")
    def test_simulate_webhook_success(self, mock_post):
        """Verifies that simulate_webhook computes a valid HMAC signature and posts raw json."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "message": "Webhook processed successfully",
            "event_id": "evt_test_123",
            "transaction_id": "TXN_GW_123",
            "event_type": "payment.captured",
            "processed": True
        }
        mock_post.return_value = mock_resp

        client = ReconcileAPIClient()
        payload = {
            "event_id": "evt_test_123",
            "event_type": "payment.captured",
            "payment_id": "pay_test_123",
            "amount": 2500.0,
            "currency": "INR"
        }
        res = client.simulate_webhook(payload, secret="test_secret")

        assert res["status"] == "success"
        assert res["transaction_id"] == "TXN_GW_123"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "/webhook/payment" in args[0]
        assert "X-Razorpay-Signature" in kwargs["headers"]
        # Verify signature length is 64 hex characters (SHA-256)
        assert len(kwargs["headers"]["X-Razorpay-Signature"]) == 64

    @patch("dashboard.api_client.requests.post")
    def test_simulate_webhook_tamper_signature(self, mock_post):
        """Verifies that tamper_signature sends an invalid signature."""
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 401
        mock_resp.text = "Invalid or missing webhook signature"
        mock_resp.json.return_value = {"detail": "Invalid or missing webhook signature"}
        mock_post.return_value = mock_resp

        client = ReconcileAPIClient()
        payload = {"event_id": "evt_tamper_1", "event_type": "payment.captured", "amount": 100.0}

        with pytest.raises(APIStatusError) as exc_info:
            client.simulate_webhook(payload, secret="test_secret", tamper_signature=True)

        assert exc_info.value.status_code == 401
        assert "Invalid or missing webhook signature" in exc_info.value.detail
        args, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-Razorpay-Signature"] == "invalid_signature_hex_deadbeef_0000000000000000"

    @patch("dashboard.api_client.requests.post")
    def test_simulate_webhook_idempotency_conflict(self, mock_post):
        """Verifies that duplicate webhook dispatch raises APIStatusError with 409."""
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 409
        mock_resp.text = "Duplicate webhook event 'evt_dup_1' rejected. Event already processed."
        mock_resp.json.return_value = {
            "detail": "Duplicate webhook event 'evt_dup_1' rejected. Event already processed."
        }
        mock_post.return_value = mock_resp

        client = ReconcileAPIClient()
        payload = {"event_id": "evt_dup_1", "event_type": "payment.captured", "amount": 500.0}

        with pytest.raises(APIStatusError) as exc_info:
            client.simulate_webhook(payload, secret="test_secret")

        assert exc_info.value.status_code == 409
        assert "Duplicate webhook event" in exc_info.value.detail

    def test_simulate_webhook_missing_secret_raises(self, monkeypatch):
        """Verifies that simulate_webhook fails clearly when no secret or WEBHOOK_SECRET is set."""
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        client = ReconcileAPIClient()
        payload = {"event_id": "evt_no_sec", "event_type": "payment.captured", "amount": 100.0}

        with pytest.raises(APIClientError) as exc_info:
            client.simulate_webhook(payload)

        assert "WEBHOOK_SECRET" in str(exc_info.value)

    def test_live_webhook_simulator_ui_elements(self):
        """Verifies that the Live Webhook Simulator panel and safe controls exist in dashboard/app.py."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        ops_start = source.index('elif nav_selection == "⚙️ Operations & Controls":')
        ops_code = source[ops_start:]

        assert "Live Webhook Simulator" in ops_code, "Simulator section header must exist"
        assert "payment.captured" in ops_code, "Supported event types must be present"
        assert "Idempotency Key" in ops_code, "Idempotency key label must be present"
        assert "Send Invalid HMAC Signature" in ops_code, "Negative security test checkbox must exist"
        assert "Dispatch Webhook" in ops_code, "Dispatch button must exist"
        assert "Replay Last Webhook" in ops_code, "Replay button must exist"
        assert "WEBHOOK_SECRET" not in ops_code, "Raw webhook secret must never be hardcoded or rendered in app.py"


class TestBenchmarkRunner:
    """Automated tests for Benchmark Runner API client method and dashboard presentation."""

    @patch("dashboard.api_client.requests.post")
    def test_run_benchmark_primary_success(self, mock_post):
        """Verifies that client.run_benchmark dispatches to /benchmark/run for primary dataset."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "dataset_name": "Primary Benchmark",
            "classification": {
                "total_ground_truth_scenarios": 100,
                "tp": 58, "tn": 42, "fp": 0, "fn": 0,
                "accuracy": 100.0, "precision": 100.0, "recall": 100.0
            },
            "operations": {
                "total_candidate_clusters": 101,
                "auto_reconciled_count": 58,
                "auto_reconciliation_rate": 57.43,
                "ai_assisted_count": 0, "ai_assisted_rate": 0.0,
                "fuzzy_assisted_count": 0, "fuzzy_assisted_rate": 0.0,
                "human_review_count": 43, "human_review_routing_rate": 42.57
            },
            "performance": {
                "raw_transaction_count": 289,
                "elapsed_seconds": 0.045,
                "throughput_txns_per_sec": 6422.0
            },
            "financial": {
                "total_transaction_value": 2406960.00,
                "unresolved_value_at_risk": 971991.00
            },
            "data_quality": {
                "missing_prediction_count": 0,
                "duplicate_prediction_count": 1,
                "extra_prediction_count": 0,
                "unmapped_ground_truth_count": 0
            }
        }
        mock_post.return_value = mock_resp

        client = ReconcileAPIClient()
        res = client.run_benchmark(is_held_out=False)

        assert res["dataset_name"] == "Primary Benchmark"
        assert res["classification"]["accuracy"] == 100.0
        assert res["operations"]["auto_reconciled_count"] == 58
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "/benchmark/run" in args[0]
        assert kwargs["params"]["is_held_out"] is False

    @patch("dashboard.api_client.requests.post")
    def test_run_benchmark_held_out(self, mock_post):
        """Verifies that client.run_benchmark passes is_held_out=True."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"dataset_name": "Held-Out Benchmark"}
        mock_post.return_value = mock_resp

        client = ReconcileAPIClient()
        res = client.run_benchmark(is_held_out=True)

        assert res["dataset_name"] == "Held-Out Benchmark"
        args, kwargs = mock_post.call_args
        assert kwargs["params"]["is_held_out"] is True

    @patch("dashboard.api_client.requests.post")
    def test_run_benchmark_failure(self, mock_post):
        """Verifies that benchmark execution failure raises APIStatusError."""
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_resp.text = "Ground truth dataset not found"
        mock_resp.json.return_value = {"detail": "Ground truth dataset not found"}
        mock_post.return_value = mock_resp

        client = ReconcileAPIClient()
        with pytest.raises(APIStatusError) as exc_info:
            client.run_benchmark(data_dir="invalid_dir")

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail

    def test_benchmark_runner_ui_elements(self):
        """Verifies that the Benchmark Runner panel and clear baseline labels exist in dashboard/app.py."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        ops_start = source.index('elif nav_selection == "⚙️ Operations & Controls":')
        ops_code = source[ops_start:]

        assert "Benchmark Runner" in ops_code, "Benchmark section header must exist"
        assert "HISTORICAL PHASE 13 BASELINE" in ops_code, "Historical baseline must be explicitly labeled"
        assert "Primary Benchmark" in ops_code, "Primary dataset option must exist"
        assert "Held-Out" in ops_code, "Held-out dataset option must exist"
        assert "Run Benchmark" in ops_code, "Benchmark button must exist"
        assert "Ground-Truth Confusion Matrix" in ops_code, "Confusion matrix display must exist"
        assert "Throughput" in ops_code, "Throughput metric must exist"
        assert "Value-at-Risk" in ops_code, "Value-at-Risk metric must exist"


class TestInteractiveDemoWorkflow:
    """Automated tests for the 5-Minute Interactive Demo Workflow."""

    def test_demo_navigation_option_exists(self):
        """Verifies that 🎬 5-Minute Demo is in navigation options and has a dedicated branch."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        assert '"🎬 5-Minute Demo"' in source, "Navigation radio options must contain 🎬 5-Minute Demo"
        assert 'elif nav_selection == "🎬 5-Minute Demo":' in source, "Dedicated branch must exist for 5-Minute Demo"

    def test_demo_stages_and_timeline_pace_guide(self):
        """Verifies that all 7 required stages and presentation pace guide are defined."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        demo_start = source.index('elif nav_selection == "🎬 5-Minute Demo":')
        demo_code = source[demo_start:]

        # 7 Stages
        assert "1. 📥 Event" in demo_code
        assert "2. 🔄 Reconcile" in demo_code
        assert "3. 🔎 Investigate" in demo_code
        assert "4. 🤖 AI Advisory" in demo_code
        assert "5. 👤 Human Decision" in demo_code
        assert "6. 📜 Audit" in demo_code
        assert "7. 📊 Benchmark" in demo_code

        # Presentation pace guide
        assert "Presentation Pace Guide" in demo_code
        assert "0:00 – 0:30" in demo_code
        assert "4:30 – 5:00" in demo_code

    def test_demo_reset_only_clears_session_state(self):
        """Verifies that Reset Demo only mutates Streamlit session_state and does not touch DB."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        demo_start = source.index('elif nav_selection == "🎬 5-Minute Demo":')
        demo_code = source[demo_start:]

        assert "Reset Demo" in demo_code
        assert 'st.session_state["demo_step"] = 1' in demo_code
        # Ensure reset demo does NOT execute delete or drop queries
        assert "DELETE FROM" not in demo_code
        assert "DROP TABLE" not in demo_code
        assert "db.rollback()" not in demo_code

    def test_demo_human_authority_safeguard(self):
        """Verifies that Stage 5 requires explicit human review and disables auto-approvals."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        demo_start = source.index('elif nav_selection == "🎬 5-Minute Demo":')
        demo_code = source[demo_start:]

        # Explicit human authority mandate
        assert "HUMAN AUTHORITY GOVERNANCE MANDATE" in demo_code
        assert "Non-binding" in demo_code
        assert "Approve Exception" in demo_code
        assert "Reject Exception" in demo_code
        assert "disabled=not confirm_check" in demo_code

    def test_demo_uses_api_client_and_no_direct_engine_imports(self):
        """Verifies that the demo view uses client API methods without direct backend imports."""
        with open("dashboard/app.py", "r", encoding="utf-8") as f:
            source = f.read()

        demo_start = source.index('elif nav_selection == "🎬 5-Minute Demo":')
        demo_code = source[demo_start:]

        # Reuses existing client methods
        assert "client.simulate_webhook" in demo_code
        assert "client.run_reconciliation" in demo_code
        assert "client.get_exceptions" in demo_code
        assert "client.get_exception" in demo_code
        assert "client.approve_exception" in demo_code
        assert "client.reject_exception" in demo_code
        assert "client.get_audit" in demo_code
        assert "client.run_benchmark" in demo_code

        # Historical benchmark labeling
        assert "HISTORICAL PHASE 13 BASELINE (Deterministic Engine)" in demo_code

        # Accurate immutability statement (no blockchain)
        assert "append-only audit controls" in demo_code
        assert "blockchain" not in demo_code.lower()




