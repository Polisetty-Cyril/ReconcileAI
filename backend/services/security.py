"""
ReconcileAI - Security & Cryptographic Utilities (Phase 10)
Provides HMAC SHA-256 signature generation and constant-time verification for payment webhooks.
"""

import hmac
import hashlib
from typing import Optional, Union

def generate_webhook_signature(raw_body: bytes, secret: Union[str, bytes]) -> str:
    """
    Computes an HMAC SHA-256 signature for raw request body bytes using a shared secret.
    """
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    else:
        secret_bytes = secret

    return hmac.new(secret_bytes, raw_body, hashlib.sha256).hexdigest()

def verify_webhook_signature(
    raw_body: bytes,
    signature: Optional[str],
    secret: Optional[str]
) -> bool:
    """
    Verifies an incoming webhook signature using constant-time comparison.
    Fails safely (returns False) if secret is missing/empty or signature is missing/empty.
    """
    if not secret or not secret.strip():
        # Fail safely: refuse to verify/accept unsigned webhooks without a valid secret configured
        return False

    if not signature or not signature.strip():
        return False

    computed = generate_webhook_signature(raw_body, secret)
    return hmac.compare_digest(computed.lower(), signature.strip().lower())
