"""Loopback HTTP client of ``thytrader-research`` (facade).

Re-exports the per-concern clients in ``__all__`` so the CLI dispatch keeps one
module: strategies (:mod:`~thytrader.research.http_strategies`), backtests
(:mod:`~thytrader.research.http_backtests`), studies and jobs
(:mod:`~thytrader.research.http_studies`), and campaigns
(:mod:`~thytrader.research.http_campaigns`). Patch a client's transport where the
client looks it up (its own module), not here.
"""

from __future__ import annotations

from thytrader.research.http_backtests import (
    backtest_model,
    explain_bars,
    export_results,
    list_results,
    show_evidence,
    show_result,
    submit_backtest,
    window_fields,
)
from thytrader.research.http_campaigns import (
    create_campaign,
    economics,
    list_campaigns,
    refresh_campaign,
    show_campaign,
)
from thytrader.research.http_strategies import (
    bulk_delete_strategies,
    bulk_delete_tagged,
    clone_strategy,
    create_strategy,
    delete_strategy,
    import_strategy,
    list_strategies,
    list_templates,
    save_strategy,
    show_snapshot,
    show_strategy,
    show_template,
    tagged_strategy_ids,
)
from thytrader.research.http_studies import (
    ASYNC_SUBMIT_TIMEOUT_SECONDS,
    cancel_research_job,
    find_study_by_request,
    list_research_jobs,
    list_studies,
    plan_study,
    show_backtest_job,
    show_research_job,
    show_study,
    submit_study,
)

__all__ = [
    "ASYNC_SUBMIT_TIMEOUT_SECONDS",
    "backtest_model",
    "bulk_delete_strategies",
    "bulk_delete_tagged",
    "cancel_research_job",
    "clone_strategy",
    "create_campaign",
    "create_strategy",
    "delete_strategy",
    "economics",
    "explain_bars",
    "export_results",
    "find_study_by_request",
    "import_strategy",
    "list_campaigns",
    "list_research_jobs",
    "list_results",
    "list_strategies",
    "list_studies",
    "list_templates",
    "plan_study",
    "refresh_campaign",
    "save_strategy",
    "show_backtest_job",
    "show_campaign",
    "show_evidence",
    "show_research_job",
    "show_result",
    "show_snapshot",
    "show_strategy",
    "show_study",
    "show_template",
    "submit_backtest",
    "submit_study",
    "tagged_strategy_ids",
    "window_fields",
]
