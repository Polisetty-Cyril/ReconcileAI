# ReconcileAI — Autonomous Multi-Source Payment Reconciliation System

**Track 04: AI Finance Controller | Razorpay AI Buildathon**

> [!NOTE]
> **Educational/Competition Prototype**: This project is an educational prototype built for the Razorpay AI Buildathon (Track 04). It is inspired by modern payment gateway and banking reconciliation architectures and utilizes 100% synthetic data with no real credentials or financial transactions.

---

## 🎯 Executive Summary & Mission
**ReconcileAI** is an AI Finance Controller designed to autonomously close the payment reconciliation loop across **Payment Gateways**, **Bank Statements**, and **ERP Accounting Ledgers**.

It solves real-world finance-operations challenges:
- **Heterogeneous Schemas**: Resolves differences in timestamps, currencies, reference IDs, and naming conventions.
- **Dual-Layer Reconciliation**: Uses 10 high-speed deterministic rules + RapidFuzz similarity matching + structured AI discrepancy reasoning.
- **Bounded Financial Autonomy**: Strict deterministic financial safety policies ensure high similarity or confidence scores never bypass hard invariants (e.g., amount mismatch, duplicate prevention, anomaly limits).
- **Human-in-the-Loop Exception Management**: Routes uncertain or discrepant transactions to a structured human review queue.
- **Full Traceability & Honest Benchmarking**: Maintains an immutable audit trail and evaluates model decisions against an unexposed ground truth dataset to measure honest Accuracy, Precision, Recall, Throughput, and Value at Risk.

---

## 📐 System Architecture

```
Financial Sources (Gateway, Bank, ERP)
               ↓
    Ingestion & ETL Layer
               ↓
    Canonical Normalization
               ↓
   SQLite Transaction Database
               ↓
 AI Finance Controller Orchestration
   (Deterministic Rules -> RapidFuzz -> AI Discrepancy Reasoning)
               ↓
  Decision & Bounded Policy Engine
   (Hard Safety Controls: Amount, Duplicate, Anomaly)
               ↓
  ├── Auto-Reconcile (High Confidence + Safe)
  ├── AI-Assisted Review (Medium Risk)
  └── Human Exception Queue (Unresolved / Discrepant)
               ↓
   Immutable Audit Log & Ground Truth Benchmark
```

---

## 📁 Repository Structure

```
ReconcileAI/
├── backend/
│   ├── main.py            # FastAPI Application & Endpoints
│   ├── config.py          # Environment & Application Settings
│   ├── models/            # SQLAlchemy Database Models (Phase 4)
│   ├── schemas/           # Pydantic Schemas (Phase 4)
│   ├── routes/            # Modular API Route Handlers
│   └── services/          # Matching, Fuzzy, AI, Policy, Webhook & Audit Services
├── dashboard/
│   └── app.py             # Streamlit Dashboard UI
├── data/                  # Synthetic Datasets & Ground Truth (Phase 2 & 3)
├── scripts/
│   └── generate_data.py   # Synthetic Data Generator with Fixed Seed
├── evaluation/
│   ├── benchmark.py       # Evaluation & Accuracy Engine
│   └── metrics.py         # Performance & Throughput Metrics
├── tests/
│   └── test_phase1.py     # Automated Test Suite for Phase 1
├── requirements.txt       # Python Dependencies
├── .env.example           # Environment Configuration Template
└── README.md              # Project Documentation
```

---

## 🚀 Quick Start (Phase 1 Baseline)

### 1. Environment Setup
```powershell
pip install -r requirements.txt
cp .env.example .env
```

### 2. Run Automated Verification Tests
```powershell
pytest -v tests/test_phase1.py
```

### 3. Launch FastAPI Backend
```powershell
python backend/main.py
```
*API will run at `http://127.0.0.1:8000` (Docs available at `http://127.0.0.1:8000/docs`).*

### 4. Launch Streamlit Dashboard
```powershell
streamlit run dashboard/app.py
```
*Dashboard will open at `http://localhost:8501`.*
