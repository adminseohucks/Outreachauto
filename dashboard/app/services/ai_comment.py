"""
VPS AI Client for LinkedPilot v2.

Sends post text and candidate comments to a remote VPS AI service with
HMAC-SHA256 authentication. Falls back to random comment selection if the
VPS is unreachable.
"""

import hashlib
import hmac
import json
import logging
import random
import time
from typing import List

import httpx

from app.config import VPS_AI_URL, VPS_API_KEY, VPS_HEALTH_URL, VPS_SSL_VERIFY

logger = logging.getLogger(__name__)


def _sign_request(timestamp: str, body: str) -> str:
    """
    Create an HMAC-SHA256 signature of ``timestamp + body`` using the API key.

    Args:
        timestamp: Unix timestamp as a string.
        body: JSON-encoded request body.

    Returns:
        Hex-encoded HMAC signature.
    """
    message = f"{timestamp}{body}"
    signature = hmac.new(
        VPS_API_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature


async def ask_ai_for_comment(
    post_text: str,
    comments: List[str],
) -> dict:
    """
    Send post text and candidate comments to VPS AI for selection.

    The request is authenticated via HMAC-SHA256.

    Args:
        post_text: The LinkedIn post's text content.
        comments: List of candidate comment strings.

    Returns:
        {
            selected_index: int,
            comment_text: str,
            confidence: float,
        }

    On failure, returns a random comment with confidence=0.
    """
    if not comments:
        return {"selected_index": -1, "comment_text": "", "confidence": 0.0}

    timestamp = str(int(time.time()))
    payload = {"post_text": post_text, "comments": comments}
    body = json.dumps(payload, separators=(",", ":"))

    signature = _sign_request(timestamp, body)

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": VPS_API_KEY,
        "X-Request-Timestamp": timestamp,
        "X-Signature": signature,
    }

    try:
        async with httpx.AsyncClient(verify=VPS_SSL_VERIFY, timeout=30.0) as client:
            response = await client.post(
                VPS_AI_URL,
                content=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            return {
                "selected_index": data.get("selected_index", 0),
                "comment_text": data.get("comment_text", comments[0]),
                "confidence": float(data.get("confidence", 0.0)),
            }

    except Exception as exc:
        logger.warning("VPS AI request failed, falling back to random: %s", exc)
        idx = random.randint(0, len(comments) - 1)
        return {
            "selected_index": idx,
            "comment_text": comments[idx],
            "confidence": 0.0,
        }


async def check_vps_health() -> dict:
    """
    Check VPS AI service health.

    Returns:
        {status: str, latency_ms: float}
    """
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(verify=VPS_SSL_VERIFY, timeout=10.0) as client:
            response = await client.get(VPS_HEALTH_URL)
            elapsed_ms = (time.monotonic() - start) * 1000
            response.raise_for_status()

            return {
                "status": "healthy",
                "latency_ms": round(elapsed_ms, 2),
            }

    except Exception as exc:
        logger.warning("VPS health check failed: %s", exc)
        return {
            "status": "unreachable",
            "latency_ms": -1,
        }
