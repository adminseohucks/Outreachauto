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


async def generate_ai_comment(
    post_text: str,
    existing_comments: List[str] | None = None,
    tone: str = "professional",
) -> dict:
    """Ask VPS AI to generate an original comment based on post content.

    Reads the post text and existing comments, then generates a short,
    professional, appreciable comment in the same tone.

    Args:
        post_text: The LinkedIn post's text content.
        existing_comments: Up to 5 existing comments for tone context.
        tone: Desired tone (default: professional).

    Returns:
        {comment_text: str, confidence: float}

    On failure, returns empty comment_text with confidence=0.
    """
    if not post_text:
        return {"comment_text": "", "confidence": 0.0}

    timestamp = str(int(time.time()))
    payload = {
        "post_text": post_text,
        "existing_comments": (existing_comments or [])[:5],
        "tone": tone,
    }
    body = json.dumps(payload, separators=(",", ":"))

    signature = _sign_request(timestamp, body)

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": VPS_API_KEY,
        "X-Request-Timestamp": timestamp,
        "X-Signature": signature,
    }

    # Build the generate-comment URL from the base VPS_AI_URL
    base_url = VPS_AI_URL.rsplit("/", 1)[0] if "/" in VPS_AI_URL else VPS_AI_URL
    generate_url = f"{base_url}/generate-comment"

    try:
        async with httpx.AsyncClient(verify=VPS_SSL_VERIFY, timeout=30.0) as client:
            response = await client.post(
                generate_url,
                content=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            comment = data.get("comment_text", "").strip()
            confidence = float(data.get("confidence", 0.0))

            if comment:
                logger.info(
                    "AI generated comment (confidence=%.2f): %.80s...",
                    confidence, comment,
                )
            return {"comment_text": comment, "confidence": confidence}

    except Exception as exc:
        logger.warning("VPS AI generate-comment failed: %s", exc)
        # Fallback: pick a generic professional comment so campaign doesn't stall
        fallback = _pick_fallback_comment()
        if fallback:
            logger.info("Using fallback comment: %s", fallback)
            return {"comment_text": fallback, "confidence": 0.1}
        return {"comment_text": "", "confidence": 0.0}


# Generic fallback comments used when VPS AI is unreachable
_FALLBACK_COMMENTS = [
    "Great insights, thanks for sharing!",
    "Really appreciate you sharing this perspective.",
    "This is very insightful, thank you for posting.",
    "Valuable thoughts, thanks for putting this out there.",
    "Well said! Thanks for sharing this with your network.",
    "Interesting perspective, appreciate you sharing.",
    "Great post, this resonates with me. Thanks for sharing!",
    "Thanks for sharing these valuable insights.",
    "Really thoughtful post, appreciate the perspective.",
    "This is spot on. Thanks for sharing your thoughts.",
]


def _pick_fallback_comment() -> str:
    """Pick a random fallback comment."""
    return random.choice(_FALLBACK_COMMENTS) if _FALLBACK_COMMENTS else ""


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
