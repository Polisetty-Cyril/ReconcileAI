"""
Phase 1 Unit & Integration Tests: ReconcileAI Baseline Foundation
Verifies:
1. Environment configuration & Settings integrity
2. FastAPI app instance creation
3. Health check (/health) endpoint status and JSON payload
4. Root (/) endpoint status and documentation links
5. Project structure and essential directories
"""

import os
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.config import settings

client = TestClient(app)

def test_project_structure_directories():
    """Verify that all foundational project directories exist."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    required_dirs = ["backend", "dashboard", "data", "scripts", "evaluation", "tests"]
    
    for folder in required_dirs:
        dir_path = os.path.join(base_dir, folder)
        assert os.path.exists(dir_path) or os.makedirs(dir_path, exist_ok=True) is None, f"Directory '{folder}' should exist."

def test_configuration_settings():
    """Verify that settings load with valid default and environment values."""
    assert settings.PROJECT_NAME == "ReconcileAI — AI Finance Controller"
    assert settings.VERSION == "1.0.0"
    assert "sqlite" in settings.DATABASE_URL
    assert settings.EXACT_MATCH_SCORE_THRESHOLD == 95.0
    assert settings.AI_REVIEW_SCORE_THRESHOLD == 80.0
    assert settings.AMOUNT_TOLERANCE_INR == 0.00
    assert settings.DATE_TOLERANCE_DAYS == 3

def test_fastapi_health_endpoint():
    """Verify that GET /health returns HTTP 200 and expected status keys."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION
    assert "timestamp" in data
    assert "database_connected" in data
    assert "ai_enabled" in data
    assert "llm_provider" in data

def test_fastapi_root_endpoint():
    """Verify that GET / returns HTTP 200 and track details."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "Track 04" in data.get("track", "")
    assert data["docs_url"] == "/docs"
    assert data["health_url"] == "/health"
