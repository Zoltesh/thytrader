"""Portfolio store contracts with in-memory (tests) and disabled (no database) stores.

:class:`PortfolioStore` persists portfolios, sleeves, and the append-only journal;
:class:`PortfolioBacktestStore` queues portfolio backtest jobs and keeps their canonical
results; :class:`PortfolioRuntimeStore` keeps each deployed portfolio's runtime state
(breaker latch, equity baselines) and the manager's proposals (ADR 0091). PostgreSQL
implements all three in one transactional class
(:mod:`thytrader.persistence.postgres_portfolios`). Every mutation re-reads the aggregate,
checks the caller's revision, plans the change with :mod:`thytrader.portfolios.rules`, and
writes the plan and its journal entries together.

The contracts live in :mod:`thytrader.portfolios.store_contracts`, the stores in
:mod:`thytrader.portfolios.store_memory` and :mod:`thytrader.portfolios.store_disabled`,
and the shared backtest journal entry in :mod:`thytrader.portfolios.store_journal`;
every name other modules import from here is re-exported (``__all__``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.portfolios.store_contracts import (
    DEPLOYED_MESSAGE,
    SLEEVE_DEPLOYED_MESSAGE,
    PortfolioBacktestNotFoundError,
    PortfolioBacktestStore,
    PortfolioRuntimeStore,
    PortfolioStorage,
    PortfolioStore,
)
from thytrader.portfolios.store_disabled import DisabledPortfolioStore
from thytrader.portfolios.store_journal import backtest_journal_entry
from thytrader.portfolios.store_memory import InMemoryPortfolioStore

if TYPE_CHECKING:
    # Type-only callback aliases (never bound at runtime, so not in ``__all__``); re-exported
    # for ``TYPE_CHECKING`` importers of this module.
    from thytrader.portfolios.store_contracts import (  # noqa: F401
        ProposalBuilder,
        ProposalSettler,
    )

__all__ = [
    "DEPLOYED_MESSAGE",
    "SLEEVE_DEPLOYED_MESSAGE",
    "DisabledPortfolioStore",
    "InMemoryPortfolioStore",
    "PortfolioBacktestNotFoundError",
    "PortfolioBacktestStore",
    "PortfolioRuntimeStore",
    "PortfolioStorage",
    "PortfolioStore",
    "backtest_journal_entry",
]
