"""
ReconcileAI - Streamlit Application Shell (Phase 1 Baseline)
UI dashboard interface for the AI Finance Controller.
"""

import streamlit as st
import requests
from datetime import datetime

st.set_page_config(
    page_title="ReconcileAI — AI Finance Controller",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
    .status-card {
        background-color: #F3F4F6;
        border-radius: 10px;
        padding: 1.2rem;
        border-left: 5px solid #2563EB;
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">ReconcileAI — AI Finance Controller</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Autonomous Multi-Source Payment Reconciliation System | Razorpay AI Buildathon Track 04</div>', unsafe_allow_html=True)

# Sidebar
st.sidebar.title("💳 ReconcileAI")
st.sidebar.markdown("**Phase 1: Foundation & Architecture**")
st.sidebar.divider()

api_url = st.sidebar.text_input("Backend API URL", value="http://127.0.0.1:8000")
st.sidebar.divider()
st.sidebar.info("""
**Architecture Modules:**
- 📥 Multi-Source Ingestion
- 🔄 Canonical Normalization
- ⚖️ Rule & Fuzzy Matching
- 🤖 AI Discrepancy Reasoning
- 🛡️ Bounded Policy Engine
- 📋 Human Exception Queue
- 📜 Immutable Audit Trail
- 📊 Real-Time Benchmark
""")

# Main View
tabs = st.tabs(["🏛️ System Overview", "🔌 API Health Check", "📐 Architecture Map"])

with tabs[0]:
    st.markdown("""
    ### Welcome to ReconcileAI
    
    **ReconcileAI** is an AI-powered Finance Controller prototype that closes the finance-operations loop across:
    1. **Payment Gateway Records** (Captured/Failed payments, fees, taxes)
    2. **Bank Statements** (Credits/Debits, UTRs, Value Dates)
    3. **ERP / Accounting Ledgers** (Invoices, Customers, Expected Payments)
    
    #### Core Value Propositions:
    - **Multi-Source Ingestion & Normalization**: Automatically unifies heterogeneous financial schemas.
    - **Dual-Layer Matching**: High-speed deterministic rules + RapidFuzz similarity + AI discrepancy analysis.
    - **Bounded Autonomy**: Deterministic financial safety policies strictly prevent hallucinated or illegal auto-reconciliations.
    - **Human-in-the-Loop**: Discrepancies and high-risk cases are queued for one-click reviewer resolution.
    - **Full Auditability & Measured Benchmark**: Complete audit trail and ground-truth backed accuracy & throughput reporting.
    """)

with tabs[1]:
    st.subheader("FastAPI Backend Health Probe")
    st.write(f"Testing connectivity to `{api_url}/health`...")
    
    if st.button("Check Backend Status"):
        try:
            res = requests.get(f"{api_url}/health", timeout=3)
            if res.status_code == 200:
                data = res.json()
                st.success("✅ Backend API is reachable and healthy!")
                st.json(data)
            else:
                st.warning(f"⚠️ API returned status code {res.status_code}")
        except Exception as e:
            st.error(f"❌ Could not connect to API server at {api_url}: {str(e)}")
            st.info("Tip: Start the FastAPI backend with `python backend/main.py`.")

with tabs[2]:
    st.subheader("System Data Flow Architecture")
    st.code("""
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
    """, language="text")

st.divider()
st.caption(f"ReconcileAI v1.0.0 | Educational Prototype | Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
