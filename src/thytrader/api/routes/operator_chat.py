"""HTTP contract for in-app operator chat (LLM key + gated skill tool loop)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, Request, status

from thytrader.api.dependencies import get_operator_chat_service, get_runtime_state
from thytrader.operator_chat.credentials import OperatorChatCredentialError
from thytrader.operator_chat.loop import OperatorChatError
from thytrader.operator_chat.models import (
    ChatTranscript,
    ChatUserMessage,
    ConfirmationDecision,
    OperatorChatCredentialWrite,
    OperatorChatStatus,
)
from thytrader.operator_chat.service import OperatorChatService  # noqa: TC001 - FastAPI Depends.
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.

router = APIRouter(prefix="/api/v1/operator-chat", tags=["operator-chat"])


@router.get("/status", response_model=OperatorChatStatus)
async def get_operator_chat_status(
    service: Annotated[OperatorChatService, Depends(get_operator_chat_service)],
) -> OperatorChatStatus:
    """Return whether an LLM key is held in this API process. Never returns the key."""
    return service.status()


@router.put("/credentials", response_model=OperatorChatStatus)
async def put_operator_chat_credentials(
    body: OperatorChatCredentialWrite,
    service: Annotated[OperatorChatService, Depends(get_operator_chat_service)],
) -> OperatorChatStatus:
    """Store a pasted LLM API key server-side. Coinbase fields are rejected."""
    try:
        return service.replace_credentials(body)
    except OperatorChatCredentialError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None


@router.delete("/credentials", response_model=OperatorChatStatus)
async def delete_operator_chat_credentials(
    service: Annotated[OperatorChatService, Depends(get_operator_chat_service)],
) -> OperatorChatStatus:
    """Drop the process-held LLM key."""
    return service.clear_credentials()


@router.get("/transcript", response_model=ChatTranscript)
async def get_operator_chat_transcript(
    service: Annotated[OperatorChatService, Depends(get_operator_chat_service)],
) -> ChatTranscript:
    """Return the in-process transcript without secrets."""
    return service.transcript()


@router.post("/reset", response_model=ChatTranscript)
async def post_operator_chat_reset(
    service: Annotated[OperatorChatService, Depends(get_operator_chat_service)],
) -> ChatTranscript:
    """Clear conversation state. Does not clear Coinbase credentials."""
    return service.reset()


@router.post("/messages", response_model=ChatTranscript)
async def post_operator_chat_message(
    body: ChatUserMessage,
    request: Request,
    service: Annotated[OperatorChatService, Depends(get_operator_chat_service)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> ChatTranscript:
    """Send one user turn through the skill-lane tool loop."""
    try:
        return await service.send_message(
            app=request.app,
            settings=runtime.settings,
            body=body,
        )
    except OperatorChatError as error:
        raise _chat_error(error) from None


@router.post("/confirmations/{confirmation_id}", response_model=ChatTranscript)
async def post_operator_chat_confirmation(
    confirmation_id: UUID,
    body: ConfirmationDecision,
    request: Request,
    service: Annotated[OperatorChatService, Depends(get_operator_chat_service)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> ChatTranscript:
    """Confirm or reject one pending mutation. Live still needs i_understand_live."""
    try:
        return await service.decide_confirmation(
            app=request.app,
            settings=runtime.settings,
            confirmation_id=confirmation_id,
            decision=body,
        )
    except OperatorChatError as error:
        raise _chat_error(error) from None


def _chat_error(error: OperatorChatError) -> HTTPException:
    """Map chat failures without echoing secrets."""
    message = str(error)
    code = (
        status.HTTP_409_CONFLICT
        if "Paste an LLM API key" in message
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=message)
