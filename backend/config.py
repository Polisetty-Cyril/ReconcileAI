"""
ReconcileAI - Configuration Module
Manages application settings, database URLs, webhook secrets, and AI execution modes.
"""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "ReconcileAI — AI Finance Controller"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "Autonomous Multi-Source Payment Reconciliation System"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./reconcile_ai.db")
    
    # Webhook Security
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "reconcile_ai_secret_key_buildathon_2026")
    
    # AI Mode Configuration
    AI_ENABLED: bool = os.getenv("AI_ENABLED", "false").lower() in ("true", "1", "yes")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "heuristic")  # "heuristic", "openai", "groq", "gemini"
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    
    # API & Dashboard
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
    API_PORT: int = int(os.getenv("API_PORT", 8000))
    DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", 8501))
    
    # Financial Safety Thresholds
    EXACT_MATCH_SCORE_THRESHOLD: float = 95.0
    AI_REVIEW_SCORE_THRESHOLD: float = 80.0
    AMOUNT_TOLERANCE_INR: float = 0.00  # Default 0 tolerance for strict financial reconciliation
    DATE_TOLERANCE_DAYS: int = 3  # Acceptable date gap between gateway captured date and bank value date
    ANOMALY_ZSCORE_THRESHOLD: float = 2.5  # Standard deviations for amount anomaly

    # Phase 7 — Fuzzy Matching Thresholds
    FUZZY_MATCH_THRESHOLD: float = 85.0   # >= this → FUZZY_MATCHED (auto-resolved with note)
    FUZZY_REVIEW_THRESHOLD: float = 70.0  # >= this but < FUZZY_MATCH_THRESHOLD → FUZZY_REVIEW

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

settings = Settings()
