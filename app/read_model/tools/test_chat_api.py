"""TEST / DEBUG ONLY — read-model chat endpoint.

A disposable shell around ``ask.py``'s ``answer_question`` so the read model can be
driven from a chat page instead of the CLI. It contains NO intelligence: it only
resolves the token, runs the existing flow, and returns the answer + the generated
query + the raw rows (so a human can see which half failed).

Mounted on a separate router, clearly NOT under /api/v1. Auth is crude on purpose
(token in the request body / Authorization header / TEST_CHAT_TOKEN env) — no login.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.core.database import engine
from app.read_model.tools.ask import AuthError, answer_question

router = APIRouter()


class TestChatRequest(BaseModel):
    message: str
    token: Optional[str] = None     # crude auth: paste a JWT from the test page


class TestChatResponse(BaseModel):
    answer: Optional[str] = None
    generated_query: Optional[dict[str, Any]] = None
    raw_rows: list[dict[str, Any]] = []
    validation_error: Optional[dict[str, Any]] = None


def _resolve_token(body: TestChatRequest, authorization: Optional[str]) -> Optional[str]:
    if body.token:
        return body.token
    if authorization:
        return authorization.removeprefix("Bearer ").strip()
    return os.getenv("TEST_CHAT_TOKEN")


@router.post("/test-chat", response_model=TestChatResponse, tags=["TEST-DEBUG"])
async def test_chat(
    body: TestChatRequest,
    authorization: Optional[str] = Header(default=None),
) -> TestChatResponse:
    token = _resolve_token(body, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="No token: pass 'token' in the body, an Authorization header, or set TEST_CHAT_TOKEN.")

    with Session(engine) as session:
        try:
            result = await answer_question(token, body.message, session)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=f"AUTH FAILED: {exc}")
        except Exception as exc:  # throwaway: surface any failure for debugging instead of a 500
            return TestChatResponse(validation_error={"error": str(exc)})

    return TestChatResponse(**result)
