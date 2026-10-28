"""The one briefing a desk's model reads each wake, and the prompt around it (ADR 0147).

Every harness receives the same ``prompt`` text: instructions, the decision contract and the
briefing JSON. The fingerprint covers everything but the generation instant, so a wake records
exactly what the model saw. The material fingerprint also leaves out the recent wakes: it changes
only when something the model can act on changes (a new closed market bar, the desk's cash, caps,
headroom or fee rates, an open book), so a wake that would read the same picture again can be
skipped without a model call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from thytrader.agent_desk.market import MarketRow
from thytrader.agent_desk.models import DESK_CONTRACT, DeskCaps, WakeOutcome

if TYPE_CHECKING:
    from thytrader.agent_desk.models import Desk, DeskWake

_RECENT_WAKES = 8
_UNSTAMPED = {
    "generated_at",
    "fingerprint",
    "material_fingerprint",
    "config_fingerprint",
    "harness",
    "model",
    "prompt",
}
_THESIS_PREVIEW = 240


class PositionView(BaseModel):
    """One open desk book as the model sees it."""

    model_config = ConfigDict(frozen=True)

    deployment_id: str
    product_id: str
    side: str
    phase: str
    allocated_quote: Decimal
    quantity: Decimal | None = None
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    mark_price: Decimal | None = None
    equity_quote: Decimal | None = None
    unrealized_return: Decimal | None = None
    opened_at: datetime


class WakeView(BaseModel):
    """One earlier wake, compactly."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    action: str
    outcome: str
    outcome_code: str | None = None
    thesis: str = ""


class DeskView(BaseModel):
    """The desk's capital, caps and remaining headroom."""

    model_config = ConfigDict(frozen=True)

    desk_id: str
    name: str
    mode: str = "paper"
    quote_currency: str
    mandate: str
    starting_cash: Decimal
    cash: Decimal
    equity_quote: Decimal | None
    return_since_start: Decimal | None
    day_open_equity: Decimal | None
    caps: DeskCaps
    open_positions: int
    entries_today: int
    entries_left_today: int
    new_entries_allowed: bool
    entry_block_reason: str | None = None
    maker_fee_rate: Decimal | None = None
    taker_fee_rate: Decimal | None = None


class DeskBriefing(BaseModel):
    """Everything one wake needs, plus the prompt sent to the harness."""

    model_config = ConfigDict(frozen=True)

    contract: str = DESK_CONTRACT
    generated_at: datetime
    fingerprint: str = ""
    material_fingerprint: str = ""
    config_fingerprint: str = ""
    # The desk's configured harness and model this briefing was built under (not in the prompt).
    harness: str = ""
    model: str | None = None
    desk: DeskView
    positions: tuple[PositionView, ...]
    recent_wakes: tuple[WakeView, ...]
    market: tuple[MarketRow, ...]
    prompt: str = ""


def finish_briefing(briefing: DeskBriefing, *, desk: Desk) -> DeskBriefing:
    """Attach the fingerprints and the harness prompt.

    The material fingerprint covers the briefing without its recent wakes, plus the UTC date
    (a new trading day is new), the prompt instructions (a new prompt is new), ``desk``'s
    configuration fingerprint (another harness, model, mandate or caps is new) and when the
    desk last became due to wake (a resume or a new cadence is new).
    """
    body = briefing.model_dump(mode="json", exclude=_UNSTAMPED)
    configuration = config_fingerprint(desk)
    material = {key: value for key, value in body.items() if key != "recent_wakes"} | {
        "utc_date": briefing.generated_at.astimezone(UTC).date().isoformat(),
        "instructions": _digest({"text": _INSTRUCTIONS, "live": _LIVE, "paper": _PAPER}),
        "configuration": configuration,
        # A resume (or a new cadence) restarts the desk; the model hears about it.
        "due_since": None if desk.due_since is None else desk.due_since.isoformat(),
    }
    stamped = briefing.model_copy(
        update={
            "fingerprint": _digest(body),
            "material_fingerprint": _digest(material),
            "config_fingerprint": configuration,
            "harness": desk.harness,
            "model": desk.model,
        }
    )
    return stamped.model_copy(update={"prompt": _prompt(stamped)})


def config_fingerprint(desk: Desk) -> str:
    """Who trades the desk and under what instructions: harness, model, mandate, caps, mode."""
    return _digest(
        {
            "harness": desk.harness,
            "model": desk.model,
            "mandate": desk.mandate,
            "caps": desk.caps.model_dump(mode="json"),
            "mode": desk.mode,
        }
    )


def _digest(body: dict[str, object]) -> str:
    """The canonical JSON digest of a briefing body."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def wake_views(wakes: tuple[DeskWake, ...]) -> tuple[WakeView, ...]:
    """The newest answered wakes (given newest first), oldest first with short theses.

    Skipped wakes carry no thesis and are left out; callers read answered wakes only, so
    skips never crowd real ones out of the window.
    """
    answered = [wake for wake in wakes if wake.outcome is not WakeOutcome.SKIPPED]
    views = [
        WakeView(
            at=wake.started_at,
            action=wake.action.value,
            outcome=wake.outcome.value,
            outcome_code=wake.outcome_code,
            thesis=wake.thesis[:_THESIS_PREVIEW],
        )
        for wake in answered[:_RECENT_WAKES]
    ]
    return tuple(reversed(views))


_INSTRUCTIONS = """You are the trader for {desk_kind}. Each wake you read the briefing below and
make exactly one decision. ThyTrader applies it to {books}, enforces the desk caps whatever you
say, and journals your thesis; your track record is scored after fees against buy-and-hold
baselines. Not trading is a valid and often correct decision.

Rules:
- Answer with exactly one JSON object and nothing else.
- Only products in the market list. {shorts}
- Size is a fraction of desk equity (size_fraction), at most caps.max_position_fraction.
- Every open needs a stop_price and a take_profit_price on the correct sides of the entry.
- Fees: a market entry pays taker_fee_rate and a limit entry (post-only) pays maker_fee_rate;
  a stop exits at taker_fee_rate and a target (a resting limit) at maker_fee_rate. Do not
  trade moves smaller than the costs.
- To exit early, close the book by its deployment_id. Stops and targets also exit on their own.
- Use the mandate and your earlier theses for continuity; do not repeat a refused decision.

Decision contract ({contract}):
  {{"action": "no_trade", "thesis": "why"}}
  {{"action": "open", "thesis": "why", "product_id": "...", "side": {sides},
    "size_fraction": 0.1, "entry": "market"|"limit", "limit_price": 1.23 (limit only),
    "stop_price": 1.0, "take_profit_price": 1.5}}
  {{"action": "close", "thesis": "why", "deployment_id": "..."}}

Briefing:
"""
_LIVE = {
    "desk_kind": "a LIVE trading desk: every order spends real money from the account",
    "books": "real spot orders on the exchange",
    "shorts": 'Live spot cannot short: open long only, with entry "limit" (a market entry is '
    "refused; a post-only limit never fills above its price).",
    "sides": '"long"',
}
_PAPER = {
    "desk_kind": "a paper trading desk",
    "books": "paper books",
    "shorts": "Spot shorts are paper-only simulations.",
    "sides": '"long"|"short"',
}


def _prompt(briefing: DeskBriefing) -> str:
    """Instructions, contract and the briefing JSON."""
    body = briefing.model_dump(mode="json", exclude=_UNSTAMPED - {"generated_at", "fingerprint"})
    wording = _LIVE if briefing.desk.mode == "live" else _PAPER
    return _INSTRUCTIONS.format(contract=briefing.contract, **wording) + json.dumps(body, indent=1)
