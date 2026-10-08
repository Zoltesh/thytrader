"""Portfolio vocabulary: the closed literal sets and fixed bounds every portfolio module shares.

Modes, journal kinds, actors, channels, breaker reasons, sleeve issue codes, the backtest
and briefing contract ids, and the size and precision bounds the validated value types
enforce (ADR 0088).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

PortfolioMode = Literal["paper", "live"]
PORTFOLIO_MODES: Final[tuple[PortfolioMode, ...]] = ("paper", "live")

JournalKind = Literal[
    "created",
    "settings_changed",
    "sleeve_added",
    "sleeve_updated",
    "sleeve_removed",
    "weights_changed",
    "limits_changed",
    "manager_changed",
    "backtest_run",
    "deployment_started",
    "deployment_paused",
    "deployment_resumed",
    "deployment_stopped",
    "breaker_tripped",
    "breaker_reset",
    "proposal_submitted",
    "proposal_approved",
    "proposal_declined",
    "proposal_failed",
]
JOURNAL_KINDS: Final[tuple[JournalKind, ...]] = (
    "created",
    "settings_changed",
    "sleeve_added",
    "sleeve_updated",
    "sleeve_removed",
    "weights_changed",
    "limits_changed",
    "manager_changed",
    "backtest_run",
    "deployment_started",
    "deployment_paused",
    "deployment_resumed",
    "deployment_stopped",
    "breaker_tripped",
    "breaker_reset",
    "proposal_submitted",
    "proposal_approved",
    "proposal_declined",
    "proposal_failed",
)
JournalActor = Literal["operator", "system", "manager"]
"""Who changed the portfolio. ``manager`` is the manager agent acting on a proposal."""
JournalReason = Literal["operator", "strategy_deleted", "manager_proposal", "breaker"]
BreakerReason = Literal["PORTFOLIO_DAILY_LOSS_STOP", "PORTFOLIO_DRAWDOWN_STOP"]
BREAKER_REASONS: Final[tuple[BreakerReason, ...]] = (
    "PORTFOLIO_DAILY_LOSS_STOP",
    "PORTFOLIO_DRAWDOWN_STOP",
)
JOURNAL_ACTORS: Final[tuple[JournalActor, ...]] = ("operator", "system", "manager")
JournalChannel = Literal["browser", "api", "system"]
JOURNAL_CHANNELS: Final[tuple[JournalChannel, ...]] = ("browser", "api", "system")
SleeveIssueCode = Literal["strategy_invalid", "quote_currency_mismatch", "product_unknown"]

PORTFOLIO_BACKTEST_CONTRACT: Final = "thytrader-portfolio-backtest-v1"
PORTFOLIO_BRIEFING_CONTRACT: Final = "thytrader-portfolio-briefing-v1"
MAX_SLEEVES: Final = 32
MAX_NAME_LENGTH: Final = 120
MAX_NOTE_LENGTH: Final = 280
MAX_MANDATE_LENGTH: Final = 2000
MAX_SUMMARY_LENGTH: Final = 500
MAX_RATIONALE_LENGTH: Final = 2000
FRACTION_PLACES: Final = 4
QUOTE_PLACES: Final = 8
MAX_CAPITAL_QUOTE: Final = Decimal("1000000000000000")
MANAGER_PERMISSION_KEYS: Final[frozenset[str]] = frozenset(
    {"may_rebalance", "may_pause_sleeves", "may_propose_sleeves"}
)
