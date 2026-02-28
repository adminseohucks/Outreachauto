"""
VPS AI Client for LinkedPilot v2.

Sends post text and candidate comments to a remote VPS AI service with
HMAC-SHA256 authentication. Falls back to contextual comment selection if the
VPS is unreachable.  Includes post-type filtering (skip hiring, spam, etc.).
"""

import hashlib
import hmac
import json
import logging
import random
import re
import time
from typing import List, Tuple

import httpx

from app.config import VPS_AI_URL, VPS_API_KEY, VPS_HEALTH_URL, VPS_SSL_VERIFY

logger = logging.getLogger(__name__)


# ── Post-type detection ──────────────────────────────────────────────────────

# Patterns that indicate a hiring/recruitment post
_HIRING_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(we'?re|is|are)\s+hiring\b",
        r"\bjob\s+(opening|opportunity|posting|alert|vacancy)\b",
        r"\b(open|new)\s+(position|role|vacancy)\b",
        r"\blooking\s+to\s+(fill|hire)\b",
        r"\bapply\s+(now|here|today|below)\b",
        r"\bjoin\s+our\s+team\b",
        r"\blooking\s+for\s+(a|an)\s+\w+\s+(to join|candidate|professional|intern)\b",
        r"\b(vacancy|vacancies|recruitment|recruiter)\b",
        r"\b#hiring\b",
        r"\bnow\s+hiring\b",
        r"\bwe\s+are\s+currently\s+looking\b",
        r"\bcurrently\s+hiring\b",
        r"\bI'?m\s+hiring\b",
    ]
]

# Regexes to extract company name from hiring posts (tried in order)
_COMPANY_EXTRACTORS: list[re.Pattern] = [
    # "CompanyName is hiring" / "CompanyName is currently looking"
    re.compile(r"^(.+?)\s+is\s+(?:hiring|currently\s+looking)", re.IGNORECASE),
    # "Hiring at CompanyName" / "We're hiring at CompanyName"
    re.compile(r"hiring\s+(?:at|for)\s+([A-Z][A-Za-z0-9\s&.'-]+?)(?:\s*[!.,;:?]|\s+(?:we|for|and|to)\b|$)", re.IGNORECASE),
    # "Join CompanyName" / "Join our team at CompanyName"
    re.compile(r"join\s+(?:us\s+at|our\s+team\s+at|the\s+team\s+at)\s+([A-Z][A-Za-z0-9\s&.'-]+?)(?:\s*[!.,;:?]|$)", re.IGNORECASE),
    # "at CompanyName" — generic fallback
    re.compile(r"\bat\s+([A-Z][A-Za-z0-9\s&.'-]{2,40}?)(?:\s*[!.,;:?]|\s+(?:is|we|and|for|as|in)\b|$)"),
]


def detect_hiring_post(post_text: str) -> Tuple[bool, str]:
    """Detect if a post is hiring/recruitment and extract company name.

    Returns:
        (is_hiring: bool, company_name: str)
    """
    if not post_text:
        return False, ""

    is_hiring = False
    for pattern in _HIRING_PATTERNS:
        if pattern.search(post_text):
            is_hiring = True
            break

    if not is_hiring:
        return False, ""

    # Try to extract company name
    for extractor in _COMPANY_EXTRACTORS:
        m = extractor.search(post_text)
        if m:
            company = m.group(1).strip().rstrip(".")
            # Sanity: skip if too short or too long
            if 2 <= len(company) <= 50:
                return True, company

    return True, ""


def _company_to_hashtag(company_name: str) -> str:
    """Convert company name to hashtag format (no spaces).

    'Valor Behavioral Health' → 'ValorBehavioralHealth'
    'TechCorp Solutions'      → 'TechCorpSolutions'
    """
    if not company_name:
        return ""
    # Remove non-alphanumeric chars except spaces
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", company_name)
    # PascalCase: ensure first letter uppercase, preserve rest of original case
    parts = clean.split()
    return "".join((word[0].upper() + word[1:]) if word else "" for word in parts)


def generate_hiring_comment(post_text: str) -> str:
    """Generate a #hiring #CompanyName comment for a hiring post.

    Examples:
        '#hiring #ValorBehavioralHealth'
        '#hiring #Google'
        '#hiring'  (if company can't be extracted)
    """
    _, company = detect_hiring_post(post_text)

    if company:
        hashtag = _company_to_hashtag(company)
        if hashtag:
            return f"#hiring #{hashtag}"
    return "#hiring"


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
    # VPS_AI_URL is typically "https://host:port/api/suggest-comment"
    # We need           "https://host:port/api/generate-comment"
    if "/api/" in VPS_AI_URL:
        generate_url = VPS_AI_URL.split("/api/")[0] + "/api/generate-comment"
    else:
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
        # Fallback: pick a CONTEXTUAL comment so campaign doesn't stall
        fallback = _pick_contextual_fallback(post_text)
        if fallback:
            logger.info("Using contextual fallback comment: %s", fallback)
            return {"comment_text": fallback, "confidence": 0.15}
        return {"comment_text": "", "confidence": 0.0}


# ── Contextual fallback comment system ───────────────────────────────────────
# Instead of generic "Great insights!", pick a comment that matches the
# post type/topic.  Much more natural and less spammy.

_ACHIEVEMENT_COMMENTS = [
    "Congratulations! Well-deserved achievement.",
    "That's a great milestone — congratulations!",
    "Wonderful news, congratulations on this!",
    "Amazing accomplishment — well done!",
]

_TIPS_COMMENTS = [
    "Solid advice, thanks for sharing these lessons.",
    "These are practical tips — appreciate you sharing.",
    "Great takeaways, definitely noting these down.",
    "Really useful advice, thank you for this.",
]

_EVENT_COMMENTS = [
    "Sounds like a great event — thanks for sharing!",
    "Interesting event, appreciate the highlights.",
    "Great recap — thanks for sharing what you learned.",
]

_LAUNCH_COMMENTS = [
    "Exciting launch — best of luck with this!",
    "Looks promising, congratulations on the launch!",
    "Great to see this come together. All the best!",
]

_STORY_COMMENTS = [
    "Thanks for sharing your experience — really inspiring.",
    "Appreciate you being open about this. Great story.",
    "This is a powerful story, thanks for sharing.",
]

_OPINION_COMMENTS = [
    "Really thoughtful take on this, appreciate the perspective.",
    "Great point — this deserves more attention.",
    "Well said, this is an important perspective.",
]

_DEFAULT_COMMENTS = [
    "Thanks for sharing this — really valuable perspective.",
    "Appreciate you putting this out there, very insightful.",
    "Great post, this adds real value to the conversation.",
]


def _pick_contextual_fallback(post_text: str) -> str:
    """Pick a fallback comment that matches the post's topic/type."""
    if not post_text:
        return random.choice(_DEFAULT_COMMENTS)

    t = post_text.lower()

    # Achievement/milestone/celebration posts
    if any(w in t for w in [
        "congratulat", "milestone", "proud", "excited to announce",
        "thrilled", "awarded", "certified", "graduated", "promotion",
        "achievement", "accomplishment", "celebrating",
    ]):
        return random.choice(_ACHIEVEMENT_COMMENTS)

    # Tips/advice/lessons posts
    if any(w in t for w in [
        " tip", "tips ", "advice", "lesson", "learned", "mistake",
        "avoid", "here's what", "things i wish", "how to ", "guide",
    ]):
        return random.choice(_TIPS_COMMENTS)

    # Event/conference posts
    if any(w in t for w in [
        "event", "conference", "summit", "webinar", "workshop",
        "speaking", "keynote", "meetup", "panel",
    ]):
        return random.choice(_EVENT_COMMENTS)

    # Product/launch posts
    if any(w in t for w in [
        "launch", "launched", "introducing", "new feature",
        "release", "announcing", "just shipped",
    ]):
        return random.choice(_LAUNCH_COMMENTS)

    # Personal story/journey posts
    if any(w in t for w in [
        "my journey", "my story", "i remember", "years ago",
        "when i started", "looking back", "reflection",
        "i was fired", "i quit", "i left", "burnout",
    ]):
        return random.choice(_STORY_COMMENTS)

    # Opinion/thought leadership posts
    if any(w in t for w in [
        "i think", "i believe", "unpopular opinion", "hot take",
        "the truth is", "we need to", "the problem with",
        "stop doing", "start doing", "why most",
    ]):
        return random.choice(_OPINION_COMMENTS)

    # Default
    return random.choice(_DEFAULT_COMMENTS)


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
