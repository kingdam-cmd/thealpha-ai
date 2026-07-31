"""
Alpha proxy — the only place your Anthropic API key lives.

Alpha Desktop sends the signed-in user's token here. This server checks
that the token is real and that the account is premium, then makes the
Anthropic call on their behalf. The key never reaches anyone's laptop.

Deploy this to Render as a separate Web Service with:
  Build command:  pip install -r requirements-proxy.txt
  Start command:  uvicorn proxy:app --host 0.0.0.0 --port $PORT

Environment variables it needs:
  ANTHROPIC_API_KEY          your Anthropic key
  SUPABASE_URL               same project URL as the web app
  SUPABASE_SERVICE_KEY       the SERVICE ROLE key — server only, never ship it
"""

import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from anthropic import Anthropic
from supabase import create_client

DEFAULT_MODEL = "claude-sonnet-4-5"

# Clients may pick from these. An allowlist rather than free choice, so a
# request can't point the key at some unexpected or far pricier model.
ALLOWED_MODELS = {
    "claude-sonnet-4-5",
    "claude-haiku-4-5-20251001",
}

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

app = FastAPI(title="Alpha proxy")
claude = Anthropic(api_key=ANTHROPIC_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    system: str = ""
    messages: list[Message]
    max_tokens: int = 1000
    model: str = DEFAULT_MODEL


def require_premium_user(authorization: str):
    """Turn an Authorization header into a verified, premium user id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in first.")

    token = authorization.split(" ", 1)[1]

    try:
        result = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Your session has expired.")

    if not result or not result.user:
        raise HTTPException(status_code=401, detail="Your session has expired.")

    user_id = result.user.id

    try:
        profile = (
            supabase.table("profiles")
            .select("is_premium")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Profile lookup failed: {e}")

    rows = profile.data or []

    if not rows:
        raise HTTPException(
            status_code=403,
            detail=(
                "No profile found for this account. If the row exists in "
                "Supabase, the server is probably using the publishable key "
                "instead of the secret key."
            ),
        )

    if not rows[0].get("is_premium"):
        raise HTTPException(
            status_code=403,
            detail="Alpha Desktop is a premium feature.",
        )

    return user_id


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(req: ChatRequest, authorization: str = Header(default="")):
    require_premium_user(authorization)

    model = req.model if req.model in ALLOWED_MODELS else DEFAULT_MODEL

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=req.max_tokens,
            system=req.system,
            messages=[m.model_dump() for m in req.messages],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")

    text = "".join(b.text for b in resp.content if b.type == "text")
    return {"text": text}