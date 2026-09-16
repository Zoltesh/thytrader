"""Confirmation-gated tool loop over shipped HTTP skill lanes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from thytrader.agent_orchestration.service import orchestration_status
from thytrader.observability.logging import extra_redacted_secrets
from thytrader.operator.redaction import REDACTION, configured_secrets, redact_text
from thytrader.operator_chat.asgi import LocalApiError, invoke_local_json
from thytrader.operator_chat.models import (
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_ROUNDS,
    ChatRole,
    LlmToolCall,
    PendingConfirmation,
)
from thytrader.operator_chat.tools import (
    ChatTool,
    openai_tool_schemas,
    split_request,
    tool_by_name,
    yolo_tier_for,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from thytrader.config import Settings
    from thytrader.operator_chat.credentials import StoredLlmCredentials
    from thytrader.operator_chat.llm import LlmClient
    from thytrader.operator_chat.session import OperatorChatSessionStore

SYSTEM_PROMPT = """You are a ThyTrader operator chat. You use the same skill lanes as ops/:
operator (read-only diagnostics), data, research, runtime, playbook status, and memory.

Rules:
- Diagnose first. Do not invent a second unsigned trading brain.
- Call only the provided tools. They map to versioned HTTP skill APIs.
- Never ask for or echo Coinbase API keys, private keys, LLM keys, database URLs, or .env values.
- Coinbase credentials are a separate server-side surface. This chat only holds an LLM key.
- Mutations require in-app confirmation (the --confirm equivalent) unless YOLO covers that tier.
- Live start and live place-order also require the understand-live hard gate. YOLO never skips it.
- Memory mutations always need confirmation. YOLO never covers memory.
- Live place-order and set-risk-policy always need confirmation.
- The playbook never starts live. Sequence paper via runtime_start mode=paper when asked.
- Paper start and paper place-order may pass maker_fee_rate and taker_fee_rate together
  (documented assumptions, not Coinbase fees). Live rejects those fields.
- Never interpolate missing candles. Judge coverage by watch_complete.
- Never claim a mutation executed until a tool result says it did.
- Do not tell anyone to edit source, Compose, Dockerfiles, Alembic, or tests.
- Extra exchanges are out of scope. Coinbase Advanced Trade spot only.
"""


class OperatorChatError(RuntimeError):
    """Operator-facing chat failure that does not leak secrets."""


async def run_tool_loop(
    *,
    app: FastAPI,
    settings: Settings,
    credentials: StoredLlmCredentials,
    llm: LlmClient,
    session: OperatorChatSessionStore,
    secrets: tuple[str, ...],
) -> None:
    """Run up to MAX_TOOL_ROUNDS provider turns, pausing on pending confirmations."""
    tools = openai_tool_schemas()
    for _round in range(MAX_TOOL_ROUNDS):
        messages = _provider_messages(session)
        completion = await llm.complete(
            credentials=credentials,
            messages=messages,
            tools=tools,
        )
        if completion.content or completion.tool_calls:
            session.add_message(
                role=ChatRole.ASSISTANT,
                content=redact_chat_text(completion.content or "", secrets),
                tool_calls=completion.tool_calls,
            )
        if not completion.tool_calls:
            return
        paused = await _apply_tool_calls(
            app=app,
            settings=settings,
            session=session,
            secrets=secrets,
            calls=completion.tool_calls,
        )
        if paused:
            return
    session.add_message(
        role=ChatRole.ASSISTANT,
        content="Stopped after the tool-round limit. Ask a follow-up if you need more.",
    )


async def execute_confirmed_tool(
    *,
    app: FastAPI,
    settings: Settings,
    pending: PendingConfirmation,
    session: OperatorChatSessionStore,
    secrets: tuple[str, ...],
    llm: LlmClient | None,
    credentials: StoredLlmCredentials | None,
) -> None:
    """Invoke one confirmed mutation, then optionally resume the LLM loop."""
    tool = tool_by_name(pending.tool_name)
    if tool is None:
        raise OperatorChatError("Unknown pending tool.")
    result = await _invoke_tool(app, tool, pending.arguments, secrets)
    session.add_message(
        role=ChatRole.TOOL,
        content=result,
        tool_call_id=pending.tool_call_id,
        tool_name=pending.tool_name,
    )
    if llm is None or credentials is None:
        return
    await run_tool_loop(
        app=app,
        settings=settings,
        credentials=credentials,
        llm=llm,
        session=session,
        secrets=secrets,
    )


def record_rejection(
    session: OperatorChatSessionStore,
    pending: PendingConfirmation,
    secrets: tuple[str, ...],
) -> None:
    """Tell the model the operator declined the mutation."""
    session.add_message(
        role=ChatRole.TOOL,
        content=redact_chat_text(
            "Operator rejected this mutation. It was not executed.",
            secrets,
        ),
        tool_call_id=pending.tool_call_id,
        tool_name=pending.tool_name,
    )


async def _apply_tool_calls(
    *,
    app: FastAPI,
    settings: Settings,
    session: OperatorChatSessionStore,
    secrets: tuple[str, ...],
    calls: tuple[LlmToolCall, ...],
) -> bool:
    """Execute or queue each tool call. True when the turn must wait on confirmation."""
    paused = False
    for call in calls:
        if paused:
            session.add_message(
                role=ChatRole.TOOL,
                content="Not executed; waiting for operator confirmation of an earlier mutation.",
                tool_call_id=call.id,
                tool_name=call.name,
            )
            continue
        if await _handle_one_call(
            app=app,
            settings=settings,
            session=session,
            secrets=secrets,
            call=call,
        ):
            paused = True
    return paused


async def _handle_one_call(
    *,
    app: FastAPI,
    settings: Settings,
    session: OperatorChatSessionStore,
    secrets: tuple[str, ...],
    call: LlmToolCall,
) -> bool:
    """Return True when this call queued a confirmation and the loop should pause."""
    tool = tool_by_name(call.name)
    if tool is None:
        session.add_message(
            role=ChatRole.TOOL,
            content=f"Unknown tool {call.name}. Use only catalog tools.",
            tool_call_id=call.id,
            tool_name=call.name,
        )
        return False
    needs_confirm, needs_live = await _gate(app, settings, tool, call.arguments)
    if (
        tool.mutation
        and not needs_confirm
        and not needs_live
        and not await _record_yolo_skip(app, tool, call.arguments)
    ):
        needs_confirm = True
    if needs_confirm or needs_live:
        pending = session.add_pending(
            lane=tool.lane,
            tool_name=tool.name,
            summary=redact_chat_text(_summary(tool, call.arguments, needs_live), secrets),
            arguments=dict(call.arguments),
            requires_understand_live=needs_live,
            tool_call_id=call.id,
        )
        session.add_message(
            role=ChatRole.TOOL,
            content=redact_chat_text(
                "Queued for in-app confirmation "
                f"(id={pending.id}, lane={tool.lane.value}, understand_live={needs_live}). "
                "Do not claim it executed.",
                secrets,
            ),
            tool_call_id=call.id,
            tool_name=tool.name,
        )
        return True
    result = await _invoke_tool(app, tool, call.arguments, secrets)
    session.add_message(
        role=ChatRole.TOOL,
        content=result,
        tool_call_id=call.id,
        tool_name=tool.name,
    )
    return False


async def _gate(
    app: FastAPI,
    settings: Settings,
    tool: ChatTool,
    arguments: dict[str, object],
) -> tuple[bool, bool]:
    """Decide confirmation and understand-live requirements."""
    needs_live = _needs_live_ack(tool, arguments)
    if not tool.mutation:
        return False, False
    if tool.hard_gate:
        return True, needs_live
    mode = await _deployment_mode(app, tool, arguments)
    tier = yolo_tier_for(tool.yolo, mode=mode)
    status = orchestration_status(settings)
    if tier is not None and status.allows(tier):
        return False, needs_live
    return True, needs_live


def _needs_live_ack(tool: ChatTool, arguments: dict[str, object]) -> bool:
    """Live start and live place-order always keep the understand-live hard gate."""
    if tool.live_ack == "never":
        return False
    return str(arguments.get("mode", "")).lower() == "live"


async def _deployment_mode(
    app: FastAPI,
    tool: ChatTool,
    arguments: dict[str, object],
) -> str | None:
    """Resolve paper vs live for YOLO binding."""
    mode = arguments.get("mode")
    if isinstance(mode, str) and mode:
        return mode
    if tool.yolo != "deployment_mode":
        return None
    deployment_id = arguments.get("deployment_id")
    if not isinstance(deployment_id, str) or not deployment_id:
        return None
    try:
        payload = await invoke_local_json(
            app,
            method="GET",
            path=f"/api/v1/deployments/{deployment_id}",
        )
    except LocalApiError:
        return None
    if isinstance(payload, dict):
        found = payload.get("mode")
        if isinstance(found, str):
            return found
    return None


async def _record_yolo_skip(
    app: FastAPI,
    tool: ChatTool,
    arguments: dict[str, object],
) -> bool:
    """Audit a skipped confirmation. False means fail closed and require UI confirm."""
    raw_mode = arguments.get("mode")
    mode = raw_mode if isinstance(raw_mode, str) else None
    if mode is None:
        mode = await _deployment_mode(app, tool, arguments)
    tier = yolo_tier_for(tool.yolo, mode=mode)
    if tier is None:
        return True
    command = tool.name.replace("_", "-")[:64]
    try:
        await invoke_local_json(
            app,
            method="POST",
            path="/api/v1/agent-orchestration/skipped-confirmations",
            payload={"tier": tier.value, "command": command},
        )
    except LocalApiError:
        return False
    return True


async def _invoke_tool(
    app: FastAPI,
    tool: ChatTool,
    arguments: dict[str, object],
    secrets: tuple[str, ...],
) -> str:
    """Call the matching HTTP skill route in-process."""
    try:
        path, query, body = split_request(tool, arguments)
    except (KeyError, ValueError) as error:
        return redact_chat_text(f"Invalid tool arguments: {error}", secrets)
    try:
        payload = await invoke_local_json(
            app,
            method=tool.method,
            path=path,
            query=query or None,
            payload=body,
        )
    except LocalApiError as error:
        return redact_chat_text(str(error), secrets)
    rendered = json.dumps(payload, default=str, ensure_ascii=False)
    if len(rendered) > MAX_TOOL_RESULT_CHARS:
        rendered = rendered[:MAX_TOOL_RESULT_CHARS] + "…"
    return redact_chat_text(rendered, secrets)


def _summary(tool: ChatTool, arguments: dict[str, object], needs_live: bool) -> str:
    """Human-readable pending-mutation line for the confirmation card."""
    bits = [f"{tool.lane.value}:{tool.name}"]
    mode = arguments.get("mode")
    if isinstance(mode, str) and mode:
        bits.append(f"mode={mode}")
    product = arguments.get("product_id")
    if isinstance(product, str) and product:
        bits.append(product)
    if needs_live:
        bits.append("requires understand-live")
    return " · ".join(bits)


def _provider_messages(session: OperatorChatSessionStore) -> list[dict[str, object]]:
    """System prompt plus stored turns."""
    return [{"role": "system", "content": SYSTEM_PROMPT}, *session.llm_messages()]


def collect_secrets(settings: Settings, llm_secret: str | None) -> tuple[str, ...]:
    """Coinbase/settings secrets plus the process LLM key."""
    values = list(configured_secrets(settings))
    if llm_secret:
        values.append(llm_secret)
    return tuple(sorted({item for item in values if item}, key=len, reverse=True))


def redact_chat_text(text: str, secrets: tuple[str, ...]) -> str:
    """Strip known secrets from model, tool, or user text."""
    merged = tuple(
        sorted(
            {item for item in (*secrets, *extra_redacted_secrets()) if item},
            key=len,
            reverse=True,
        )
    )
    redacted = redact_text(text, merged)
    if REDACTION not in redacted and any(secret and secret in text for secret in merged):
        return REDACTION
    return redacted
