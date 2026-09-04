# ReconcileAI — REST API Reference

## 1. API Overview

**ReconcileAI** provides a high-performance RESTful API built on **FastAPI** to orchestrate multi-source financial reconciliation, operational exception governance, gateway webhook processing, and immutable audit compliance.

> [!IMPORTANT]
> **Educational & Buildathon Context:**
> The ReconcileAI API is an educational reference implementation developed for **Track 04: AI Finance Controller** in the **Razorpay AI Buildathon**. All transactions, webhooks, orders, and bank settlement identifiers are **100% synthetic** and run entirely in isolated local environments. This service does not interface with live Razorpay production merchant infrastructure or live banking networks.

### 1.1 Server Address & Interactive Documentation
* **Base URL**: `http://127.0.0.1:8000` (configurable via `API_HOST` and `API_PORT`)
* **OpenAPI Specification**: `http://127.0.0.1:8000/openapi.json`
* **Swagger UI**: [`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs)
* **ReDoc UI**: [`http://127.0.0.1:8000/redoc`](http://127.0.0.1:8000/redoc)

---

## 2. Complete Endpoint Inventory

The API exposes **19 public REST endpoints** across 8 domain categories:

| # | HTTP Method | Route Path | Tag / Domain | Purpose |
| :---: | :---: | :--- | :--- | :--- |
| **1** | `GET` | `/health` | System | Service health check, version, and configuration state |
| **2** | `GET` | `/` | System | Root discovery endpoint with documentation links |
| **3** | `POST` | `/webhook/payment` | Webhook Simulator | HMAC-secured gateway webhook ingestion & transaction creation |
| **4** | `GET` | `/exceptions` | Exception Management | Paginated query of reconciliation exceptions with filters |
| **5** | `GET` | `/exceptions/{exception_id}` | Exception Management | Detailed single exception view with AI context and SLA metrics |
| **6** | `POST` | `/exceptions/{exception_id}/approve` | Exception Management | Human reviewer approval of financial exception |
| **7** | `POST` | `/exceptions/{exception_id}/reject` | Exception Management | Human reviewer rejection of financial exception |
| **8** | `GET` | `/transactions` | Transactions | Paginated query of canonical multi-source transactions |
| **9** | `POST` | `/transactions/load-synthetic` | Transactions | Ingests and normalizes multi-source synthetic datasets |
| **10** | `POST` | `/reconcile` | Reconciliation | Triggers 3-leg deterministic, fuzzy, and AI reconciliation pipeline |
| **11** | `GET` | `/reconciliation/results` | Reconciliation | Paginated query of match clusters and resolution statuses |
| **12** | `GET` | `/audit` | Audit Trail | Read-only query of append-only, immutable audit trail events |
| **13** | `GET` | `/reports/summary` | Reports | Real-time operational summary KPIs and category breakdowns |
| **14** | `GET` | `/reports/executive` | Reports | Executive financial statement metrics and total transaction values |
| **15** | `GET` | `/reports/reconciliation` | Reports | Comprehensive multi-source reconciliation records for reporting |
| **16** | `GET` | `/reports/exceptions` | Reports | Discrepancy aging and SLA status breakdown |
| **17** | `GET` | `/reports/transactions` | Reports | Complete transaction dataset without pagination cap for export |
| **18** | `GET` | `/reports/audit` | Reports | Full regulatory audit compliance records for export |
| **19** | `POST` | `/benchmark/run` | Evaluation | Runs objective reconciliation benchmark against ground truth |

---

## 3. System & Discovery APIs

### GET `/health`
**Purpose**: Returns service liveness, software version, database connectivity, and configured AI/LLM provider state.

* **Request**: No parameters or request body.
* **Response (HTTP 200 OK)**:
```json
{
  "status": "healthy",
  "service": "ReconcileAI — AI Finance Controller",
  "version": "1.0.0",
  "timestamp": "2026-09-04T14:30:00.000000+00:00",
  "database_connected": true,
  "ai_enabled": false,
  "llm_provider": "heuristic"
}
```
* **Status Codes**:
  * `200 OK`: Service operational.

---

### GET `/`
**Purpose**: Welcome banner and direct navigation links to OpenAPI/ReDoc documentation and health check.

* **Request**: No parameters or request body.
* **Response (HTTP 200 OK)**:
```json
{
  "message": "Welcome to ReconcileAI — Autonomous Multi-Source Payment Reconciliation System",
  "docs_url": "/docs",
  "health_url": "/health",
  "track": "Razorpay AI Buildathon — Track 04: AI Finance Controller"
}
```
* **Status Codes**:
  * `200 OK`: Discovery successful.

---

## 4. Webhook Ingestion API

### POST `/webhook/payment`
**Purpose**: Ingests, cryptographically verifies, normalizes, and stores real-time payment gateway webhook events into canonical Gateway transactions. Enforces replay protection and logs security audit records.

* **Request Headers**:
  * `X-Razorpay-Signature` (*optional, string*): Hex-encoded HMAC SHA-256 signature calculated over the exact raw request body bytes using `WEBHOOK_SECRET`. (Can also be supplied via `signature` attribute in JSON payload).
* **Request Body** (`PaymentWebhookPayload`):
```json
{
  "event_id": "evt_wh_pay_9001",
  "event_type": "payment.captured",
  "payment_id": "pay_9001",
  "order_id": "ORD_9001",
  "customer_id": "CUST_9001",
  "amount": 2500.00,
  "currency": "INR",
  "payment_method": "upi",
  "fee": 45.00,
  "tax": 8.10,
  "description": "Payment for Order ORD_9001",
  "timestamp": "2026-09-04T12:00:00Z",
  "metadata": {
    "merchant_id": "MID_BUILDATHON_01"
  }
}
```
* **Validation & Constraints**:
  * `event_id`: Unique non-empty string. Used as idempotency key.
  * `event_type`: Must be one of `payment.authorized`, `payment.captured`, `payment.failed`, `refund.created`.
  * `amount`: Positive floating-point number ($> 0.00$), rounded to 2 decimal places.
  * `currency`: Three-letter ISO code (default: `"INR"`).
* **Processing Guarantees**:
  1. **HMAC SHA-256 Verification**: Checked using `hmac.compare_digest()` against the raw request byte stream.
  2. **Idempotency**: Re-submitting an existing `event_id` is rejected immediately.
  3. **Transactional Atomic Persistence**: Persists `WebhookEvent`, normalizes a `Transaction` row (`source="GATEWAY"`), and logs an `AuditLog` entry (`action="WEBHOOK_RECEIVED"`).
* **Response (HTTP 200 OK)**:
```json
{
  "status": "success",
  "message": "Webhook processed successfully.",
  "event_id": "evt_wh_pay_9001",
  "transaction_id": "pay_9001",
  "event_type": "payment.captured",
  "processed": true
}
```
* **Status Codes**:
  * `200 OK`: Successfully validated, normalized, and stored.
  * `400 Bad Request`: Payload validation or mapping failure.
  * `401 Unauthorized`: Missing or invalid HMAC SHA-256 signature. (Records `WEBHOOK_SIGNATURE_FAILED` in audit log).
  * `409 Conflict`: Duplicate `event_id` detected. (Records `WEBHOOK_DUPLICATE_REJECTED` in audit log).
  * `422 Unprocessable Entity`: Malformed JSON or schema constraint violation.
  * `500 Internal Server Error`: Server database or persistence failure.

---

## 5. Transaction Data APIs

### GET `/transactions`
**Purpose**: Retrieves a paginated list of canonical financial transactions from the ledger across Gateway, Bank, and ERP sources.

* **Query Parameters**:
  * `source` (*optional, string*): Filter by originating financial system: `GATEWAY`, `BANK`, or `ERP`.
  * `status` (*optional, string*): Filter by canonical status (e.g., `CAPTURED`, `PAID`, `CREDIT`, `FAILED`).
  * `start_date` (*optional, datetime*): Filter transactions on or after this timestamp (ISO 8601).
  * `end_date` (*optional, datetime*): Filter transactions on or before this timestamp (ISO 8601).
  * `limit` (*optional, int*, default: `50`, min: `1`, max: `500`): Maximum records to return.
  * `offset` (*optional, int*, default: `0`, min: `0`): Pagination offset.
* **Response (HTTP 200 OK)**:
```json
{
  "total": 289,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "id": 1,
      "transaction_id": "GW_TXN_001",
      "source": "GATEWAY",
      "reference_id": "pay_001",
      "order_id": "ORD_001",
      "customer_id": "CUST_001",
      "amount": 1500.00,
      "currency": "INR",
      "transaction_date": "2026-09-01T10:00:00Z",
      "status": "CAPTURED",
      "transaction_type": "PAYMENT",
      "description": "Payment Gateway Txn GW_TXN_001",
      "metadata_json": "{\"fee\": 30.0, \"tax\": 5.4, \"net_amount\": 1464.6}",
      "created_at": "2026-09-04T08:00:00Z"
    }
  ]
}
```
* **Status Codes**:
  * `200 OK`: Query successful.
  * `422 Unprocessable Entity`: Invalid query parameters (e.g. negative limit).

---

### POST `/transactions/load-synthetic`
**Purpose**: Reads, normalizes, and stores multi-source synthetic datasets (Gateway, Bank, and ERP CSVs) into the `transactions` table. Operates with built-in deduplication (upserts existing records).

* **Query Parameters**:
  * `data_dir` (*optional, string*, default: `"data"`): File system directory containing the synthetic CSV files.
  * `is_held_out` (*optional, bool*, default: `false`): If `true`, loads the reserved held-out validation dataset (`held_out_*.csv`).
* **Response (HTTP 200 OK)**:
```json
{
  "status": "SUCCESS",
  "gateway_loaded": 100,
  "bank_loaded": 95,
  "erp_loaded": 94,
  "total_loaded": 289,
  "message": "Successfully ingested 289 transactions from 'data' (is_held_out=False)."
}
```
* **Status Codes**:
  * `200 OK`: Datasets ingested and persisted.
  * `404 Not Found`: Synthetic dataset CSV files missing from specified directory.
  * `500 Internal Server Error`: File parsing or database ingestion error.

---

## 6. Reconciliation APIs

### POST `/reconcile`
**Purpose**: Triggers the end-to-end multi-source reconciliation pipeline via `FinanceController`. Executes deterministic 3-leg matching, fuzzy investigation (RapidFuzz) on unmatched pairs, advisory AI reasoning on discrepancies, stages actionable `OPEN` exceptions, and records audit logs.

* **Request**: No request body required.
* **Execution & Duplicate Safety**:
  * Checks existing candidate clusters against database state.
  * If all staged transactions are already reconciled, returns `"status": "SKIPPED"` without generating duplicate records.
  * If new or pending transactions exist, processes clusters and returns `"status": "COMPLETED"`.
* **Response (HTTP 200 OK — Fresh Run)**:
```json
{
  "status": "COMPLETED",
  "total_clusters": 101,
  "total_reconciled": 58,
  "total_review": 43,
  "total_exceptions": 43,
  "auto_reconciled_rate": 57.43,
  "unresolved_value_at_risk": 18450.00,
  "message": "Reconciliation pipeline executed successfully. Processed 101 results."
}
```
* **Response (HTTP 200 OK — Repeated Run / Skipped)**:
```json
{
  "status": "SKIPPED",
  "total_clusters": 101,
  "total_reconciled": 58,
  "total_review": 43,
  "total_exceptions": 43,
  "auto_reconciled_rate": 57.43,
  "unresolved_value_at_risk": 18450.00,
  "message": "All 289 staged transactions across 101 candidate clusters have already been reconciled. No new transactions to process."
}
```
* **Status Codes**:
  * `200 OK`: Pipeline executed or safely skipped.
  * `400 Bad Request`: No staged transactions found in the database.
  * `500 Internal Server Error`: Processing failure during reconciliation.

---

### GET `/reconciliation/results`
**Purpose**: Retrieves a paginated list of reconciliation match clusters, matching methods, AI advisory output, discrepancy amounts, and resolution status.

* **Query Parameters**:
  * `final_decision` (*optional, string*): Filter by decision (e.g., `AUTO_RECONCILED`, `HUMAN_REVIEW`, `MANUAL_APPROVED`, `MANUAL_REJECTED`).
  * `is_resolved` (*optional, bool*): Filter by boolean resolution status (`true` / `false`).
  * `reconciliation_id` (*optional, string*): Exact filter for a specific reconciliation cluster ID.
  * `limit` (*optional, int*, default: `50`, min: `1`, max: `500`): Maximum records to return.
  * `offset` (*optional, int*, default: `0`, min: `0`): Pagination offset.
* **Response (HTTP 200 OK)**:
```json
{
  "total": 101,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "id": 1,
      "reconciliation_id": "REC_CLUST_001",
      "gateway_transaction_id": "GW_TXN_001",
      "bank_transaction_id": "BNK_TXN_001",
      "erp_invoice_id": "INV_001",
      "match_score": 100.0,
      "matching_method": "EXACT_THREE_WAY",
      "ai_recommendation": null,
      "ai_confidence": null,
      "ai_reasoning": null,
      "final_decision": "AUTO_RECONCILED",
      "discrepancy_amount": 0.0,
      "is_resolved": true,
      "reconciled_at": "2026-09-04T08:15:00Z"
    }
  ]
}
```
* **Status Codes**:
  * `200 OK`: Query successful.
  * `422 Unprocessable Entity`: Invalid query parameter types.

---

## 7. Exception Management & Governance APIs

### GET `/exceptions`
**Purpose**: Retrieves a paginated queue of reconciliation exceptions with composable filtering by status, severity, and category.

* **Query Parameters**:
  * `status` (*optional, string*): Filter by lifecycle status: `OPEN`, `APPROVED`, `REJECTED`, or `RESOLVED`.
  * `severity` (*optional, string*): Filter by severity: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
  * `category` (*optional, string*): Filter by category (e.g. `AMOUNT_MISMATCH`, `MISSING_BANK_TRANSACTION`, `FEE_MISMATCH`).
  * `limit` (*optional, int*, default: `50`, min: `1`): Maximum records to return.
  * `offset` (*optional, int*, default: `0`, min: `0`): Pagination offset.
* **Response (HTTP 200 OK)**:
```json
{
  "total": 43,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "id": 1,
      "exception_id": "EXC_001",
      "reconciliation_id": "REC_CLUST_012",
      "transaction_id": "GW_TXN_012",
      "category": "FEE_MISMATCH",
      "severity": "HIGH",
      "difference_amount": 45.00,
      "ai_explanation": "Gateway fee variance detected: Expected standard 2% + GST fee, but charged 3.5% surcharge.",
      "status": "OPEN",
      "reviewer_notes": null,
      "resolved_by": null,
      "resolved_at": null,
      "created_at": "2026-09-04T08:15:00Z",
      "sla_duration_hours": 4.0,
      "sla_deadline": "2026-09-04T12:15:00Z",
      "sla_status": "WARNING",
      "escalation_level": 0,
      "escalated_at": null
    }
  ]
}
```
* **Status Codes**:
  * `200 OK`: Query successful.

---

### GET `/exceptions/{exception_id}`
**Purpose**: Fetches complete detail for a single reconciliation exception record including advisory AI context and SLA tracking.

* **Path Parameters**:
  * `exception_id` (*required, string*): Unique business exception identifier (e.g., `EXC_001`).
* **Response (HTTP 200 OK)**:
```json
{
  "id": 1,
  "exception_id": "EXC_001",
  "reconciliation_id": "REC_CLUST_012",
  "transaction_id": "GW_TXN_012",
  "category": "FEE_MISMATCH",
  "severity": "HIGH",
  "difference_amount": 45.00,
  "ai_explanation": "Gateway fee variance detected: Expected standard 2% + GST fee, but charged 3.5% surcharge.",
  "status": "OPEN",
  "reviewer_notes": null,
  "resolved_by": null,
  "resolved_at": null,
  "created_at": "2026-09-04T08:15:00Z",
  "sla_duration_hours": 4.0,
  "sla_deadline": "2026-09-04T12:15:00Z",
  "sla_status": "WARNING",
  "escalation_level": 0,
  "escalated_at": null
}
```
* **Status Codes**:
  * `200 OK`: Exception found.
  * `404 Not Found`: Exception identifier does not exist.

---

### POST `/exceptions/{exception_id}/approve`
**Purpose**: Executes a human reviewer approval decision for an exception. Sets status to `APPROVED`, records `reviewer_id` and `notes`, synchronizes the associated `ReconciliationResult` (`is_resolved = True`, `final_decision = 'MANUAL_APPROVED'`), and records an immutable `AuditLog` entry.

* **Path Parameters**:
  * `exception_id` (*required, string*): Unique exception ID.
* **Request Body** (`ExceptionActionRequest`):
```json
{
  "reviewer_id": "REV_PRIYA_SHARMA",
  "notes": "Verified promotional merchant discount agreement. Approved manual adjustment."
}
```
* **Governance Constraints & Conflict Handling**:
  * **Idempotency**: If the same reviewer re-approves an already approved exception, the existing record is returned safely without duplicate logging.
  * **Conflict Rejection (HTTP 400)**: Attempting to approve an exception that was previously `REJECTED` is rejected with `HTTP 400 Bad Request`.
  * **Conflict Overwrite (HTTP 400)**: Attempting to overwrite an approval from a *different* reviewer is rejected with `HTTP 400 Bad Request`.
* **Response (HTTP 200 OK)**:
```json
{
  "id": 1,
  "exception_id": "EXC_001",
  "reconciliation_id": "REC_CLUST_012",
  "transaction_id": "GW_TXN_012",
  "category": "FEE_MISMATCH",
  "severity": "HIGH",
  "difference_amount": 45.00,
  "ai_explanation": "Gateway fee variance detected: Expected standard 2% + GST fee, but charged 3.5% surcharge.",
  "status": "APPROVED",
  "reviewer_notes": "Verified promotional merchant discount agreement. Approved manual adjustment.",
  "resolved_by": "REV_PRIYA_SHARMA",
  "resolved_at": "2026-09-04T09:30:00Z",
  "created_at": "2026-09-04T08:15:00Z",
  "sla_duration_hours": 4.0,
  "sla_deadline": "2026-09-04T12:15:00Z",
  "sla_status": "WARNING",
  "escalation_level": 0,
  "escalated_at": null
}
```
* **Status Codes**:
  * `200 OK`: Exception successfully approved and synchronized.
  * `400 Bad Request`: Conflicting state transition attempt.
  * `404 Not Found`: Exception ID not found.

---

### POST `/exceptions/{exception_id}/reject`
**Purpose**: Executes a human reviewer rejection decision for an exception. Sets status to `REJECTED`, records `reviewer_id` and `notes`, synchronizes the associated `ReconciliationResult` (`is_resolved = True`, `final_decision = 'MANUAL_REJECTED'`), and records an immutable `AuditLog` entry.

* **Path Parameters**:
  * `exception_id` (*required, string*): Unique exception ID.
* **Request Body** (`ExceptionActionRequest`):
```json
{
  "reviewer_id": "REV_PRIYA_SHARMA",
  "notes": "Bank statement confirms chargeback initiated by customer. Discrepancy rejected."
}
```
* **Governance Constraints & Conflict Handling**:
  * **Idempotency**: If the same reviewer re-rejects an already rejected exception, the existing record is returned safely.
  * **Conflict Rejection (HTTP 400)**: Attempting to reject an exception that was previously `APPROVED` is rejected with `HTTP 400 Bad Request`.
* **Response (HTTP 200 OK)**:
```json
{
  "id": 1,
  "exception_id": "EXC_001",
  "reconciliation_id": "REC_CLUST_012",
  "transaction_id": "GW_TXN_012",
  "category": "FEE_MISMATCH",
  "severity": "HIGH",
  "difference_amount": 45.00,
  "ai_explanation": "Gateway fee variance detected: Expected standard 2% + GST fee, but charged 3.5% surcharge.",
  "status": "REJECTED",
  "reviewer_notes": "Bank statement confirms chargeback initiated by customer. Discrepancy rejected.",
  "resolved_by": "REV_PRIYA_SHARMA",
  "resolved_at": "2026-09-04T09:35:00Z",
  "created_at": "2026-09-04T08:15:00Z",
  "sla_duration_hours": 4.0,
  "sla_deadline": "2026-09-04T12:15:00Z",
  "sla_status": "WARNING",
  "escalation_level": 0,
  "escalated_at": null
}
```
* **Status Codes**:
  * `200 OK`: Exception successfully rejected and synchronized.
  * `400 Bad Request`: Conflicting state transition attempt.
  * `404 Not Found`: Exception ID not found.

---

## 8. Audit Trail API

### GET `/audit`
**Purpose**: Queries the strictly append-only, immutable audit trail. Provides complete chronological transparency over system mutations, webhook signature failures, reconciliation events, AI advisory recommendations, and human reviewer decisions.

* **Query Parameters**:
  * `entity` (*optional, string*): Filter by domain entity type: `TRANSACTION`, `RECONCILIATION`, `EXCEPTION`, `WEBHOOK`.
  * `entity_id` (*optional, string*): Filter by specific business identifier.
  * `action` (*optional, string*): Filter by audited event action.
  * `limit` (*optional, int*, default: `100`, min: `1`, max: `500`): Maximum records to return.
  * `offset` (*optional, int*, default: `0`, min: `0`): Pagination offset.
* **Audited Actions**:
  * `TRANSACTION_INGESTED`: Raw transaction loaded and normalized.
  * `AUTO_RECONCILED`: Exact deterministic 3-way match established.
  * `EXCEPTION_CREATED`: Discrepancy identified and routed to review queue.
  * `FUZZY_INVESTIGATED`: RapidFuzz similarity score calculated.
  * `AI_REASONED`: Advisory root cause and remediation advice generated.
  * `EXCEPTION_APPROVED`: Human reviewer approved discrepancy.
  * `EXCEPTION_REJECTED`: Human reviewer rejected discrepancy.
  * `WEBHOOK_RECEIVED`: Webhook verified and stored.
  * `WEBHOOK_SIGNATURE_FAILED`: Webhook rejected due to cryptographic signature mismatch.
  * `WEBHOOK_DUPLICATE_REJECTED`: Duplicate webhook `event_id` rejected.
* **Response (HTTP 200 OK)**:
```json
{
  "total": 145,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "id": 1,
      "audit_id": "AUD_EXC_98F3A2B1",
      "timestamp": "2026-09-04T09:30:00Z",
      "actor": "REV_PRIYA_SHARMA",
      "action": "EXCEPTION_APPROVED",
      "entity": "EXCEPTION",
      "entity_id": "EXC_001",
      "old_value": "{\"status\": \"OPEN\"}",
      "new_value": "{\"status\": \"APPROVED\", \"resolved_by\": \"REV_PRIYA_SHARMA\", \"reviewer_notes\": \"Verified promotional discount.\"}",
      "reason": "Verified promotional discount."
    }
  ]
}
```
* **Status Codes**:
  * `200 OK`: Query successful.
  * `422 Unprocessable Entity`: Invalid pagination bounds.

---

## 9. Reporting APIs

All reporting endpoints are **strictly read-only**, computing real-time metrics and aggregates from the SQLite database.

### GET `/reports/summary`
**Purpose**: Real-time operational summary metrics for executive dashboards.

* **Response (HTTP 200 OK)**:
```json
{
  "total_transactions": 289,
  "total_reconciliation_results": 101,
  "total_auto_reconciled": 58,
  "total_exceptions": 43,
  "open_exceptions": 41,
  "approved_exceptions": 1,
  "rejected_exceptions": 1,
  "auto_reconciliation_rate": 57.43,
  "unresolved_amount_inr": 18450.00,
  "exceptions_by_severity": {
    "CRITICAL": 5,
    "HIGH": 12,
    "MEDIUM": 20,
    "LOW": 6
  },
  "exceptions_by_category": {
    "AMOUNT_MISMATCH": 15,
    "MISSING_BANK_TRANSACTION": 10,
    "FEE_MISMATCH": 12,
    "TIMING_DELAY": 6
  },
  "sla_status_breakdown": {
    "ON_TRACK": 25,
    "WARNING": 10,
    "BREACHED": 6
  },
  "decision_breakdown": {
    "AUTO_RECONCILED": 58,
    "HUMAN_REVIEW": 41,
    "MANUAL_APPROVED": 1,
    "MANUAL_REJECTED": 1
  }
}
```

---

### GET `/reports/executive`
**Purpose**: Executive financial summary including total financial turnover in INR, net reconciliation rates, and aggregate Value-at-Risk.

* **Response (HTTP 200 OK)**:
```json
{
  "total_transactions": 289,
  "total_transaction_value_inr": 2406960.00,
  "total_reconciliation_results": 101,
  "total_auto_reconciled": 58,
  "auto_reconciliation_rate": 57.43,
  "total_exceptions": 43,
  "open_exceptions": 41,
  "approved_exceptions": 1,
  "rejected_exceptions": 1,
  "unresolved_amount_inr": 18450.00,
  "exceptions_by_severity": { "CRITICAL": 5, "HIGH": 12, "MEDIUM": 20, "LOW": 6 },
  "exceptions_by_category": { "AMOUNT_MISMATCH": 15, "FEE_MISMATCH": 12 },
  "sla_status_breakdown": { "ON_TRACK": 25, "WARNING": 10, "BREACHED": 6 },
  "decision_breakdown": { "AUTO_RECONCILED": 58, "HUMAN_REVIEW": 41 },
  "generated_at": "2026-09-04T14:30:00Z"
}
```

---

### GET `/reports/reconciliation`
**Purpose**: Granular three-leg reconciliation candidate cluster records for reporting and export. Supports filtering by `final_decision`, `is_resolved`, and `reconciliation_id`.

* **Response (HTTP 200 OK)**:
```json
{
  "total": 101,
  "items": [
    {
      "reconciliation_id": "REC_CLUST_001",
      "gateway_transaction_id": "GW_TXN_001",
      "bank_transaction_id": "BNK_TXN_001",
      "erp_invoice_id": "INV_001",
      "matching_method": "EXACT_THREE_WAY",
      "match_score": 100.0,
      "discrepancy_amount": 0.0,
      "ai_recommendation": null,
      "ai_confidence": null,
      "ai_reasoning": null,
      "final_decision": "AUTO_RECONCILED",
      "is_resolved": true,
      "reconciled_at": "2026-09-04T08:15:00Z"
    }
  ]
}
```

---

### GET `/reports/exceptions`
**Purpose**: Discrepancy aging report with SLA deadlines, escalation tiers, and reviewer attribution. Supports filtering by `status`, `severity`, `category`, and `sla_status`.

* **Response (HTTP 200 OK)**:
```json
{
  "total": 43,
  "items": [
    {
      "exception_id": "EXC_001",
      "reconciliation_id": "REC_CLUST_012",
      "transaction_id": "GW_TXN_012",
      "category": "FEE_MISMATCH",
      "severity": "HIGH",
      "difference_amount": 45.00,
      "status": "OPEN",
      "sla_duration_hours": 4.0,
      "sla_deadline": "2026-09-04T12:15:00Z",
      "sla_status": "WARNING",
      "escalation_level": 0,
      "escalated_at": null,
      "created_at": "2026-09-04T08:15:00Z",
      "resolved_at": null,
      "resolved_by": null,
      "reviewer_notes": null
    }
  ]
}
```

---

### GET `/reports/transactions`
**Purpose**: Uncapped transaction dataset matching query filters for full CSV/Excel export. Supports filtering by `source`, `status`, `start_date`, and `end_date`.

* **Response (HTTP 200 OK)**:
```json
{
  "total": 289,
  "items": [
    {
      "transaction_id": "GW_TXN_001",
      "source": "GATEWAY",
      "reference_id": "pay_001",
      "order_id": "ORD_001",
      "customer_id": "CUST_001",
      "amount": 1500.00,
      "currency": "INR",
      "transaction_date": "2026-09-01T10:00:00Z",
      "status": "CAPTURED",
      "transaction_type": "PAYMENT",
      "description": "Payment Gateway Txn GW_TXN_001",
      "metadata_json": "{\"fee\": 30.0, \"tax\": 5.4, \"net_amount\": 1464.6}",
      "created_at": "2026-09-04T08:00:00Z"
    }
  ]
}
```

---

### GET `/reports/audit`
**Purpose**: Complete immutable audit log dataset matching filters for regulatory compliance export. Supports filtering by `entity`, `entity_id`, `action`, and `actor`.

* **Response (HTTP 200 OK)**:
```json
{
  "total": 145,
  "items": [
    {
      "audit_id": "AUD_EXC_98F3A2B1",
      "timestamp": "2026-09-04T09:30:00Z",
      "actor": "REV_PRIYA_SHARMA",
      "action": "EXCEPTION_APPROVED",
      "entity": "EXCEPTION",
      "entity_id": "EXC_001",
      "old_value": "{\"status\": \"OPEN\"}",
      "new_value": "{\"status\": \"APPROVED\", \"resolved_by\": \"REV_PRIYA_SHARMA\"}",
      "reason": "Verified promotional discount."
    }
  ]
}
```

---

## 10. Benchmark & Evaluation API

### POST `/benchmark/run`
**Purpose**: Executes the non-mutating evaluation benchmark harness against synthetic ground-truth datasets. Computes classification metrics (Accuracy, Precision, Recall), operational statistics, financial Value-at-Risk, and processing throughput.

* **Query Parameters**:
  * `is_held_out` (*optional, bool*, default: `false`): If `true`, benchmarks against the reserved held-out split (`held_out_*.csv`). If `false`, benchmarks against primary ground truth.
  * `data_dir` (*optional, string*, default: `"data"`): Directory containing benchmark CSV files.
* **Evaluation Terminology & Denominators**:
  * **100 Ground-Truth Scenarios**: Denominator for classification metrics (Accuracy, Precision, Recall).
  * **101 Candidate Match Clusters**: Denominator for operational routing (Auto-Reconciled Rate, Review Routing Rate).
  * **289 Raw Source Transactions**: Denominator for processing throughput (~6,400+ txns/sec) and Total Volume.
* **Historical Baseline Note**:
  The benchmark measures the **deterministic baseline engine** (`DeterministicReconciliationEngine`). In this standalone baseline test, fuzzy investigation and AI reasoning are not invoked, yielding historical baseline rates of `fuzzy_assisted_rate: 0.0%` and `ai_assisted_rate: 0.0%`.
* **Response (HTTP 200 OK)**:
```json
{
  "dataset_name": "primary_ground_truth",
  "classification": {
    "total_ground_truth_scenarios": 100,
    "tp": 58,
    "tn": 42,
    "fp": 0,
    "fn": 0,
    "accuracy": 1.0,
    "precision": 1.0,
    "recall": 1.0
  },
  "operations": {
    "total_candidate_clusters": 101,
    "auto_reconciled_count": 58,
    "auto_reconciliation_rate": 57.43,
    "ai_assisted_count": 0,
    "ai_assisted_rate": 0.0,
    "fuzzy_assisted_count": 0,
    "fuzzy_assisted_rate": 0.0,
    "human_review_count": 43,
    "human_review_routing_rate": 42.57,
    "human_resolution_rate": null
  },
  "performance": {
    "raw_transaction_count": 289,
    "elapsed_seconds": 0.045,
    "throughput_txns_per_sec": 6422.2
  },
  "financial": {
    "total_transaction_value": 2406960.00,
    "unresolved_value_at_risk": 18450.00
  },
  "data_quality": {
    "missing_prediction_count": 0,
    "duplicate_prediction_count": 0,
    "extra_prediction_count": 1,
    "unmapped_ground_truth_count": 0
  }
}
```
* **Status Codes**:
  * `200 OK`: Benchmark completed successfully.
  * `404 Not Found`: Ground-truth dataset files not found.
  * `500 Internal Server Error`: Benchmark execution failure.

---

## 11. Error Handling & Standard Responses

The ReconcileAI API utilizes standard HTTP status codes and structured JSON response schemas for error reporting:

```json
{
  "detail": "Descriptive error message explaining the failure or constraint violation"
}
```

When validation fails on request headers or request bodies (Pydantic validation), FastAPI returns `HTTP 422 Unprocessable Entity` containing field-level details:

```json
{
  "detail": [
    {
      "loc": ["body", "amount"],
      "msg": "Amount must be a positive number greater than zero.",
      "type": "value_error"
    }
  ]
}
```

### Standard Status Code Reference
| HTTP Status | Meaning | Typical Trigger in ReconcileAI |
| :---: | :--- | :--- |
| **200 OK** | Success | Standard response for successful queries, updates, and executions. |
| **400 Bad Request** | Business Logic Error | Conflicting exception approval/rejection or empty transaction reconciliation request. |
| **401 Unauthorized** | Authentication / Cryptographic Failure | Missing or invalid `X-Razorpay-Signature` HMAC hash on `/webhook/payment`. |
| **404 Not Found** | Resource Missing | Non-existent `exception_id` or missing synthetic dataset CSV files. |
| **409 Conflict** | Duplicate Request | Duplicate webhook `event_id` replay intercepted. |
| **422 Unprocessable** | Schema Validation Failure | Malformed JSON body, negative pagination offset, or missing required fields. |
| **500 Internal Error** | Server Error | Unhandled database or system exception during pipeline execution. |

---

## 12. Security Model & Invariants

1. **HMAC SHA-256 Webhook Verification**: Computed strictly over the incoming raw byte stream (`request.body()`). Comparison uses `hmac.compare_digest()` to eliminate timing-attack vulnerabilities.
2. **Idempotency Enforcement**: Intercepts replay attempts on webhook `event_id`s, human decision re-submissions, and repeated SLA notifications.
3. **Immutable Audit Persistence**: Audit logs are protected by SQLAlchemy lifecycle hooks (`before_update`, `before_delete`, `do_orm_execute`). The `/audit` endpoint is strictly read-only (`GET`).
4. **Synthetic Financial Isolation**: Built entirely on synthetic data. No production banking API keys or live customer credentials are utilized.

---

## 13. Read-Only vs. Mutating API Matrix

| Endpoint | HTTP Method | Read-Only? | Mutates Database / System State? |
| :--- | :---: | :---: | :--- |
| `GET /health` | `GET` | **Yes** | No |
| `GET /` | `GET` | **Yes** | No |
| `POST /webhook/payment` | `POST` | **No** | **Yes** (Persists WebhookEvent, Transaction, AuditLog) |
| `GET /exceptions` | `GET` | **Yes** | No |
| `GET /exceptions/{exception_id}` | `GET` | **Yes** | No |
| `POST /exceptions/{exception_id}/approve` | `POST` | **No** | **Yes** (Transitions Exception, ReconciliationResult, AuditLog) |
| `POST /exceptions/{exception_id}/reject` | `POST` | **No** | **Yes** (Transitions Exception, ReconciliationResult, AuditLog) |
| `GET /transactions` | `GET` | **Yes** | No |
| `POST /transactions/load-synthetic` | `POST` | **No** | **Yes** (Loads and normalizes canonical transactions) |
| `POST /reconcile` | `POST` | **No** | **Yes** (Generates ReconciliationResults, Exceptions, AuditLogs) |
| `GET /reconciliation/results` | `GET` | **Yes** | No |
| `GET /audit` | `GET` | **Yes** | No |
| `GET /reports/summary` | `GET` | **Yes** | No |
| `GET /reports/executive` | `GET` | **Yes** | No |
| `GET /reports/reconciliation` | `GET` | **Yes** | No |
| `GET /reports/exceptions` | `GET` | **Yes** | No |
| `GET /reports/transactions` | `GET` | **Yes** | No |
| `GET /reports/audit` | `GET` | **Yes** | No |
| `POST /benchmark/run` | `POST` | **Yes** | No (Computes benchmark in memory against ground truth) |

---

## 14. Human Decision Authority

A foundational architectural invariant of ReconcileAI is the **strict separation between AI advisory context and human decision authority**:

* **Deterministic Matching**: Automatically reconciles exact, mathematical 3-way matches (`match_score = 100.0`, `discrepancy = 0.00`).
* **Fuzzy Engine**: Quantifies textual and reference similarity (RapidFuzz) as factual evidence.
* **AI Finance Controller**: Evaluates discrepancies and fuzzy evidence to generate explanatory reasoning and suggested actions. **The AI has zero resolution authority.**
* **Human-in-the-Loop Sign-Off**: Exceptions can **only** be marked `APPROVED` or `REJECTED` via the explicit human decision endpoints (`POST /exceptions/{id}/approve`, `POST /exceptions/{id}/reject`).
* **Identity & Attribution**: Every approval or rejection strictly mandates a verified `reviewer_id` and explanatory `notes`, which are recorded immutably in the `AuditLog`.

---

## 15. Complete API Lifecycle Workflow

The following sequence illustrates a typical end-to-end integration workflow using standard cURL commands:

### Step 1: Ingest Synthetic Multi-Source Transactions
```bash
curl -X POST "http://127.0.0.1:8000/transactions/load-synthetic?data_dir=data"
```

### Step 2: Trigger Multi-Source Reconciliation
```bash
curl -X POST "http://127.0.0.1:8000/reconcile"
```

### Step 3: Query Open Exceptions
```bash
curl -X GET "http://127.0.0.1:8000/exceptions?status=OPEN&limit=10"
```

### Step 4: Review Exception Details & AI Advisory Context
```bash
curl -X GET "http://127.0.0.1:8000/exceptions/EXC_001"
```

### Step 5: Execute Human Reviewer Approval
```bash
curl -X POST "http://127.0.0.1:8000/exceptions/EXC_001/approve" \
  -H "Content-Type: application/json" \
  -d '{"reviewer_id": "REV_PRIYA_SHARMA", "notes": "Approved variance based on merchant fee tier verification."}'
```

### Step 6: Verify Immutable Audit Record
```bash
curl -X GET "http://127.0.0.1:8000/audit?entity_id=EXC_001"
```

### Step 7: Retrieve Executive KPIs
```bash
curl -X GET "http://127.0.0.1:8000/reports/executive"
```

---

## 16. Local Development & Server Startup

### Prerequisites
* Python 3.10+
* Virtual environment with project dependencies installed:
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### Configuration (`.env`)
Create or verify your local `.env` configuration (refer to `.env.example`):
```ini
API_HOST=127.0.0.1
API_PORT=8000
DATABASE_URL=sqlite:///./reconcile_ai.db
WEBHOOK_SECRET=reconcile_ai_secret_key_buildathon_2026
AI_ENABLED=false
LLM_PROVIDER=heuristic
```

### Starting the FastAPI Server
Start the development server using Uvicorn:
```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
Once started, the API will be available at `http://127.0.0.1:8000` with interactive Swagger UI documentation at `http://127.0.0.1:8000/docs`.
