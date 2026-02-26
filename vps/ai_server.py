"""
LinkedPilot v2 - VPS AI Comment Server
FastAPI server with HMAC authentication that uses Ollama Phi-3
to select the best LinkedIn comment for a given post.
"""

import hashlib
import hmac
import os
import random
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
EXPECTED_API_KEY: str = os.getenv("VPS_API_KEY", "")
OLLAMA_URL: str = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL: str = "phi3:mini"
OLLAMA_TIMEOUT: int = 30  # seconds
TIMESTAMP_TOLERANCE: int = 300  # seconds

SERVER_START_TIME: float = time.time()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="LinkedPilot AI Comment Server",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SuggestCommentRequest(BaseModel):
    post_text: str = Field(..., max_length=5000)
    comments: list[str] = Field(..., max_length=50)

    @field_validator("comments")
    @classmethod
    def validate_comments(cls, v: list[str]) -> list[str]:
        if len(v) == 0:
            raise ValueError("comments list must not be empty")
        for i, comment in enumerate(v):
            if len(comment) > 300:
                raise ValueError(
                    f"Comment at index {i} exceeds 300 character limit"
                )
        return v


class SuggestCommentResponse(BaseModel):
    selected_index: int
    comment_text: str
    confidence: float


class HealthResponse(BaseModel):
    status: str
    model: str
    uptime_seconds: float


# ---------------------------------------------------------------------------
# HMAC Authentication Middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def hmac_auth_middleware(request: Request, call_next):
    # Allow health endpoint without auth
    if request.url.path == "/health":
        return await call_next(request)

    # --- Check 1: API key header ---
    api_key = request.headers.get("X-API-Key", "")
    if not EXPECTED_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"detail": "Server API key not configured"},
        )
    if not hmac.compare_digest(api_key, EXPECTED_API_KEY):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key"},
        )

    # --- Check 2: Timestamp freshness ---
    timestamp_str = request.headers.get("X-Request-Timestamp", "")
    if not timestamp_str:
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing request timestamp"},
        )
    try:
        request_ts = float(timestamp_str)
    except ValueError:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid timestamp format"},
        )
    if abs(time.time() - request_ts) > TIMESTAMP_TOLERANCE:
        return JSONResponse(
            status_code=401,
            content={"detail": "Request timestamp out of tolerance"},
        )

    # --- Check 3: HMAC-SHA256 signature ---
    signature = request.headers.get("X-Signature", "")
    if not signature:
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing request signature"},
        )

    body_bytes = await request.body()
    message = (timestamp_str + body_bytes.decode("utf-8")).encode("utf-8")
    expected_sig = hmac.new(
        api_key.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid HMAC signature"},
        )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_prompt(post_text: str, comments: list[str]) -> str:
    numbered = "\n".join(
        f"{i + 1}. {c}" for i, c in enumerate(comments)
    )
    return (
        "You are a LinkedIn comment selector. "
        "Given a post and a list of predefined comments, "
        "pick the ONE comment that best fits the post. "
        "Reply ONLY with the comment number (1-N).\n\n"
        f"Post: {post_text}\n\n"
        f"Comments:\n{numbered}"
    )


def _parse_ollama_index(response_text: str, num_comments: int) -> int | None:
    """Extract the first valid integer (1-based) from the Ollama response."""
    import re

    matches = re.findall(r"\b(\d+)\b", response_text)
    for m in matches:
        idx = int(m)
        if 1 <= idx <= num_comments:
            return idx
    return None


async def _call_ollama(prompt: str) -> str | None:
    """Send a prompt to the local Ollama instance and return the response text."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "").strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/suggest-comment", response_model=SuggestCommentResponse)
async def suggest_comment(req: SuggestCommentRequest):
    prompt = _build_prompt(req.post_text, req.comments)
    ollama_response = await _call_ollama(prompt)

    selected_index: int | None = None
    confidence: float = 0.0

    if ollama_response is not None:
        selected_index = _parse_ollama_index(ollama_response, len(req.comments))
        if selected_index is not None:
            confidence = 0.85

    # Fallback: pick a random comment if Ollama failed or returned garbage
    if selected_index is None:
        selected_index = random.randint(1, len(req.comments))
        confidence = 0.1

    # Convert to 0-based for array access
    zero_based = selected_index - 1
    return SuggestCommentResponse(
        selected_index=zero_based,
        comment_text=req.comments[zero_based],
        confidence=confidence,
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        model=OLLAMA_MODEL,
        uptime_seconds=round(time.time() - SERVER_START_TIME, 2),
    )
