"""Application service for in-app operator chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.operator_chat.llm import LlmClientError, OpenAICompatibleClient
from thytrader.operator_chat.loop import (
    OperatorChatError,
    collect_secrets,
    execute_confirmed_tool,
    record_rejection,
    redact_chat_text,
    run_tool_loop,
)
from thytrader.operator_chat.models import (
    ChatRole,
    ChatTranscript,
    ChatUserMessage,
    ConfirmationDecision,
    OperatorChatCredentialWrite,
    OperatorChatStatus,
)

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi import FastAPI

    from thytrader.config import Settings
    from thytrader.operator_chat.credentials import OperatorChatCredentialStore
    from thytrader.operator_chat.llm import LlmClient
    from thytrader.operator_chat.session import OperatorChatSessionStore


class OperatorChatService:
    """Coordinate LLM credentials, transcript, and gated tool execution."""

    def __init__(
        self,
        *,
        credentials: OperatorChatCredentialStore,
        sessions: OperatorChatSessionStore,
        llm: LlmClient | None = None,
    ) -> None:
        """Bind process-lifetime stores. Coinbase keys are not accepted here."""
        self._credentials = credentials
        self._sessions = sessions
        self._llm = llm or OpenAICompatibleClient()

    def status(self) -> OperatorChatStatus:
        """Return redacted LLM configuration flags."""
        return self._credentials.status()

    def transcript(self) -> ChatTranscript:
        """Return the current conversation."""
        return self._sessions.transcript(self._credentials.status())

    def replace_credentials(self, write: OperatorChatCredentialWrite) -> OperatorChatStatus:
        """Store a pasted LLM key in this API process."""
        return self._credentials.replace(write)

    def clear_credentials(self) -> OperatorChatStatus:
        """Drop the process-held LLM key."""
        return self._credentials.clear()

    def reset(self) -> ChatTranscript:
        """Clear the transcript and pending mutations."""
        self._sessions.reset()
        return self.transcript()

    async def send_message(
        self,
        *,
        app: FastAPI,
        settings: Settings,
        body: ChatUserMessage,
    ) -> ChatTranscript:
        """Append a user turn and run the skill-lane tool loop."""
        stored = self._credentials.get()
        if stored is None:
            raise OperatorChatError(
                "Paste an LLM API key before chatting. This is not a Coinbase key."
            )
        secrets = collect_secrets(settings, stored.api_key.get_secret_value())
        self._sessions.add_message(
            role=ChatRole.USER,
            content=redact_chat_text(body.content, secrets),
        )
        try:
            await run_tool_loop(
                app=app,
                settings=settings,
                credentials=stored,
                llm=self._llm,
                session=self._sessions,
                secrets=secrets,
            )
        except LlmClientError as error:
            raise OperatorChatError(str(error)) from error
        return self.transcript()

    async def decide_confirmation(
        self,
        *,
        app: FastAPI,
        settings: Settings,
        confirmation_id: UUID,
        decision: ConfirmationDecision,
    ) -> ChatTranscript:
        """Execute or reject one pending mutation."""
        pending = self._sessions.pop_pending(confirmation_id)
        if pending is None:
            raise OperatorChatError("No matching pending confirmation.")
        stored = self._credentials.get()
        secrets = collect_secrets(
            settings,
            None if stored is None else stored.api_key.get_secret_value(),
        )
        if not decision.confirmed:
            record_rejection(self._sessions, pending, secrets)
            return self.transcript()
        if pending.requires_understand_live and not decision.i_understand_live:
            self._sessions.restore_pending(pending)
            raise OperatorChatError(
                "Live mutations require i_understand_live=true. Confirmation was not consumed."
            )
        try:
            await execute_confirmed_tool(
                app=app,
                settings=settings,
                pending=pending,
                session=self._sessions,
                secrets=secrets,
                llm=self._llm,
                credentials=stored,
            )
        except LlmClientError as error:
            raise OperatorChatError(str(error)) from error
        return self.transcript()
