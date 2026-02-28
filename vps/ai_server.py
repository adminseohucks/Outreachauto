"""
LinkedPilot v2 - VPS AI Comment Server
FastAPI server with HMAC authentication that uses Ollama Phi-3
to select the best LinkedIn comment for a given post, and also
generate original AI comments based on post context.
"""

import hashlib
import hmac
import os
import random
import time
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
    version="3.0.0",
    docs_url=None,
    redoc_url=None,
)

# CORS for Chrome extension requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["chrome-extension://*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
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


class GenerateCommentRequest(BaseModel):
    post_text: str = Field(..., max_length=5000)
    post_author: str = Field(default="", max_length=200)
    existing_comments: list[str] = Field(default_factory=list, max_length=10)
    commenter_name: str = Field(default="", max_length=200)
    tone: str = Field(default="professional", max_length=50)
    language: str = Field(default="english", max_length=30)


class GenerateCommentResponse(BaseModel):
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


def _is_hiring_post(post_text: str) -> bool:
    """Quick check if a post is about hiring/recruitment."""
    import re
    hiring_keywords = [
        r"\b(we'?re|is|are)\s+hiring\b", r"\bjob\s+(opening|opportunity)\b",
        r"\bapply\s+(now|here|today)\b", r"\bjoin\s+our\s+team\b",
        r"\bnow\s+hiring\b", r"\bcurrently\s+hiring\b", r"\b#hiring\b",
        r"\b(vacancy|recruitment)\b", r"\blooking\s+to\s+(fill|hire)\b",
    ]
    for kw in hiring_keywords:
        if re.search(kw, post_text, re.IGNORECASE):
            return True
    return False


def _build_generate_prompt(
    post_text: str,
    post_author: str,
    existing_comments: list[str],
    commenter_name: str,
    tone: str,
    language: str,
) -> str:
    """Build a prompt for generating an original LinkedIn comment."""

    # Special prompt for hiring posts: just extract company and return hashtags
    if _is_hiring_post(post_text):
        return (
            "This is a LinkedIn hiring/recruitment post. "
            "Your task: extract the company name from the post and reply with EXACTLY "
            "two hashtags: #hiring #CompanyName (company name without spaces, CamelCase).\n"
            "Examples:\n"
            '- Post: "Google is hiring..." → #hiring #Google\n'
            '- Post: "Valor Behavioral Health is hiring..." → #hiring #ValorBehavioralHealth\n'
            '- Post: "We\'re hiring at TechCorp Solutions" → #hiring #TechCorpSolutions\n\n'
            "Reply with ONLY the two hashtags, nothing else.\n\n"
            f"Post:\n{post_text}\n\n"
            "Your comment:"
        )

    existing_section = ""
    if existing_comments:
        numbered = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(existing_comments))
        existing_section = (
            f"\nExisting comments on this post (for context — do NOT repeat these):\n{numbered}\n"
        )

    author_section = f" by {post_author}" if post_author else ""
    name_section = f" Your name is {commenter_name}." if commenter_name else ""

    lang_instruction = ""
    if language.lower() != "english":
        lang_instruction = f" Write the comment in {language}."

    return (
        f"You are a LinkedIn professional writing a comment on a post{author_section}.\n"
        f"{name_section}\n"
        f"Tone: {tone} — be genuine, specific to the post content, and add value.\n"
        f"Rules:\n"
        f"- Write ONLY the comment text, nothing else\n"
        f"- Keep it 1-3 sentences, under 280 characters\n"
        f"- Be specific to the post content, not generic\n"
        f"- Sound natural and human, not like a bot\n"
        f"- Add a unique perspective or ask a thoughtful question\n"
        f"- Do NOT start with 'Great post' or 'Thanks for sharing'\n"
        f"- Do NOT use hashtags or emojis\n"
        f"- Do NOT repeat what existing comments already say\n"
        f"{lang_instruction}\n"
        f"{existing_section}\n"
        f"Post:\n{post_text}\n\n"
        f"Your comment:"
    )


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


@app.post("/api/generate-comment", response_model=GenerateCommentResponse)
async def generate_comment(req: GenerateCommentRequest):
    """Generate an original AI comment based on post content, tone, and existing comments."""
    prompt = _build_generate_prompt(
        post_text=req.post_text,
        post_author=req.post_author,
        existing_comments=req.existing_comments,
        commenter_name=req.commenter_name,
        tone=req.tone,
        language=req.language,
    )
    ollama_response = await _call_ollama(prompt)

    if ollama_response:
        # Clean up: remove quotes, extra whitespace
        comment = ollama_response.strip().strip('"').strip("'").strip()
        # Remove any prefix like "Comment:" or "Here's my comment:"
        for prefix in ["Comment:", "comment:", "Here's my comment:", "My comment:"]:
            if comment.startswith(prefix):
                comment = comment[len(prefix):].strip()
        # Limit length to 280 chars (LinkedIn best practice)
        if len(comment) > 280:
            comment = comment[:277] + "..."
        if comment:
            return GenerateCommentResponse(comment_text=comment, confidence=0.9)

    # Fallback: generate a simple generic comment
    fallbacks = [
        "Great insights! Thanks for sharing this.",
        "Really valuable perspective. Appreciate you posting this!",
        "This resonates so much. Well said!",
        "Interesting take — thanks for putting this out there.",
        "Couldn't agree more. Great post!",
    ]
    return GenerateCommentResponse(
        comment_text=random.choice(fallbacks),
        confidence=0.1,
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        model=OLLAMA_MODEL,
        uptime_seconds=round(time.time() - SERVER_START_TIME, 2),
    )
