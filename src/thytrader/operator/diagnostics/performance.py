"""Backtest and paper/live deployment performance operator reports."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal  # noqa: TC003 - runtime marks map uses Decimal at runtime
from typing import TYPE_CHECKING

from thytrader import __version__
from thytrader.backtest.cost_attribution import compute_cost_attribution
from thytrader.backtest.metrics import compute_performance_metrics
from thytrader.backtest.models import (
    BacktestEvaluationWindow,
    BacktestResult,
    backtest_evaluation_window,
)
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.products import SpotQuoteCurrency, quote_currency
from thytrader.operator.diagnostics.common import (
    _runtime_timeframe,
    _strategy_clock_and_published_quote,
    _strategy_timeframe,
)
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    PerformanceBookPayload,
    PerformancePayload,
    PerformanceReport,
    ReportStatus,
    SupportedTimeframe,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.persistence.backtest_results import (
    BacktestResultNotFoundError,
    BacktestResultUnavailableError,
    BacktestSourceSpecificationReader,
)
from thytrader.trading.ledger import effective_paper_fee_rates, ledger_from_snapshot
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.operator.service import OperatorDiagnostics
    from thytrader.trading.ledger import DeploymentLedger


async def build_performance_report(
    diagnostics: OperatorDiagnostics,
    *,
    result_fingerprint: str | None = None,
    deployment_id: UUID | None = None,
) -> PerformanceReport:
    """Return one backtest result or one paper/live runtime performance slice."""
    now = datetime.now(UTC)
    if result_fingerprint:
        return await _backtest_performance(diagnostics, result_fingerprint, now)
    if deployment_id is not None:
        return await _deployment_performance(diagnostics, deployment_id, now)
    component = ComponentReport(
        name="performance",
        status=ReportStatus.FAILED,
        reason_code="RESULT_NOT_FOUND",
        detail="Pass a result fingerprint or deployment id.",
    )
    return _empty_performance(now, (component,))


async def _strategy_quote_currency(
    diagnostics: OperatorDiagnostics, fingerprint: str
) -> SpotQuoteCurrency | None:
    """Return the published strategy quote currency, or ``None`` when unprovable."""
    _clock, quote = await _strategy_clock_and_published_quote(diagnostics, fingerprint)
    return quote


async def _backtest_performance(
    diagnostics: OperatorDiagnostics,
    result_fingerprint: str,
    now: datetime,
) -> PerformanceReport:
    """Load one reverified backtest summary as operator performance evidence."""
    try:
        result = await diagnostics.backtests.load(result_fingerprint)
    except BacktestResultNotFoundError:
        component = ComponentReport(
            name="performance",
            status=ReportStatus.FAILED,
            reason_code="RESULT_NOT_FOUND",
            detail="No immutable backtest result exists for that fingerprint.",
        )
        return _empty_performance(now, (component,))
    except BacktestResultUnavailableError:
        component = ComponentReport(
            name="performance",
            status=ReportStatus.FAILED,
            reason_code="BACKTESTS_UNAVAILABLE",
            detail="Backtest result storage is unavailable.",
        )
        return _empty_performance(now, (component,))
    summary = result.summary
    try:
        metrics = compute_performance_metrics(result)
    except TypeError, ValueError:
        metrics = None
    component = ComponentReport(
        name="performance",
        status=ReportStatus.HEALTHY,
        reason_code="BACKTEST",
        detail="Metrics are historical simulation evidence, not live fills.",
    )
    payload = PerformancePayload(
        mode="backtest",
        timeframe=await _strategy_timeframe(diagnostics, result.strategy_fingerprint),
        currency=await _strategy_quote_currency(diagnostics, result.strategy_fingerprint),
        strategy_fingerprint=result.strategy_fingerprint,
        dataset_fingerprint=result.dataset_fingerprint,
        fee_treatment="disclosed maker/taker rates on the immutable research run",
        result_fingerprint=result_fingerprint,
        deployment_id=None,
        trade_count=summary.trade_count,
        total_net_pnl=summary.total_net_pnl,
        total_return_fraction=summary.total_return_fraction,
        maximum_drawdown_fraction=summary.maximum_drawdown_fraction,
        total_spread_cost=summary.total_spread_cost,
        evaluation_bars=summary.evaluation_bars,
        metrics=metrics,
        window=await _backtest_window(diagnostics, result),
        cost_attribution=compute_cost_attribution(result),
    )
    return PerformanceReport(
        application_version=__version__,
        generated_at=now,
        overall_status=ReportStatus.HEALTHY,
        components=(component,),
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action((component,)),
        payload=payload,
    )


async def _backtest_window(
    diagnostics: OperatorDiagnostics, result: BacktestResult
) -> BacktestEvaluationWindow | None:
    """Best-effort evaluated window from the result's run; never hides the result."""
    store = diagnostics.backtests
    if not isinstance(store, BacktestSourceSpecificationReader):
        return None
    try:
        specification = await store.load_source_specification(result)
        return backtest_evaluation_window(specification, result.summary.evaluation_bars)
    except Exception:  # noqa: BLE001 - the window is advisory; the summary stands alone.
        return None


async def _deployment_performance(
    diagnostics: OperatorDiagnostics,
    deployment_id: UUID,
    now: datetime,
) -> PerformanceReport:
    """Summarize paper or live PnL from recorded fills and a disclosed last close."""
    try:
        snapshot = await diagnostics.execution.get_deployment(deployment_id)
    except Exception:  # noqa: BLE001 - store failures are redacted at this boundary.
        component = ComponentReport(
            name="performance",
            status=ReportStatus.FAILED,
            reason_code="DEPLOYMENT_UNAVAILABLE",
            detail="The deployment could not be loaded.",
        )
        return _empty_performance(now, (component,))
    deployment = snapshot.deployment
    timeframe = await _runtime_timeframe(diagnostics, deployment)
    currency, currency_warning = await _deployment_quote_currency(diagnostics, deployment)
    marks = await _deployment_marks(diagnostics, snapshot, timeframe)
    ledger = ledger_from_snapshot(snapshot, marks=marks)
    component, warnings = _deployment_ledger_component(deployment, ledger)
    if currency_warning is not None:
        warnings = (*warnings, currency_warning)
    payload = PerformancePayload(
        mode="live" if deployment.mode is DeploymentMode.LIVE else "paper",
        timeframe=timeframe,
        currency=currency,
        strategy_fingerprint=deployment.strategy_fingerprint,
        dataset_fingerprint=None,
        fee_treatment=_runtime_fee_treatment(deployment),
        result_fingerprint=None,
        deployment_id=deployment.id,
        trade_count=ledger.trade_count,
        total_net_pnl=ledger.total_net_pnl_text(),
        total_return_fraction=ledger.total_return_fraction_text(),
        maximum_drawdown_fraction=ledger.maximum_drawdown_fraction_text(),
        total_spread_cost=None,
        evaluation_bars=None,
        mark_complete=ledger.mark_complete,
        marked_exposure=(
            None if ledger.marked_exposure is None else format(ledger.marked_exposure, "f")
        ),
        books=_performance_books(ledger),
    )
    return PerformanceReport(
        application_version=__version__,
        generated_at=now,
        overall_status=component.status,
        components=(component,),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=warnings,
        recommended_next_action=recommend_next_action((component,)),
        payload=payload,
    )


async def _deployment_quote_currency(
    diagnostics: OperatorDiagnostics, deployment: Deployment
) -> tuple[SpotQuoteCurrency | None, str | None]:
    """Return the deployment quote currency plus a warning when it stays unknown.

    Strategy books inherit the published instrument quote. Discretionary and
    unreconcilable books derive the quote from the Coinbase product id, and a
    product id that does not parse leaves ``None`` so the report never asserts
    a currency it cannot prove.
    """
    if deployment.strategy_fingerprint:
        _clock, quote = await _strategy_clock_and_published_quote(
            diagnostics, deployment.strategy_fingerprint
        )
        if quote is not None:
            return quote, None
        return None, (
            "Quote currency is unknown: the published strategy could not be loaded, "
            "so PnL amounts are reported without a currency label."
        )
    try:
        return quote_currency(deployment.product_id), None
    except ValueError:
        return None, (
            f"Quote currency is unknown: product id {deployment.product_id!r} "
            "does not encode a supported quote currency."
        )


async def _last_close_mark(
    diagnostics: OperatorDiagnostics, product_id: str, timeframe: SupportedTimeframe
) -> Decimal | None:
    """Return the last closed candle close for the strategy interval, if one exists."""
    if diagnostics.market_data is None:
        return None
    try:
        preview = await diagnostics.market_data.get_preview(
            product_id, parse_candle_interval(timeframe)
        )
    except Exception:  # noqa: BLE001 - missing marks stay omitted, not invented.
        return None
    candles = preview.quality.candles
    if not candles:
        return None
    return candles[-1].close


async def _deployment_marks(
    diagnostics: OperatorDiagnostics,
    snapshot: DeploymentSnapshot,
    timeframe: SupportedTimeframe,
) -> dict[str, Decimal]:
    """Return last-close marks for every open product book on one deployment."""
    marks: dict[str, Decimal] = {}
    products = {
        resolved_product_id(position.product_id, snapshot.deployment)
        for position in snapshot_positions(snapshot)
    }
    products.add(snapshot.deployment.product_id)
    for product_id in sorted(products):
        mark = await _last_close_mark(diagnostics, product_id, timeframe)
        if mark is not None:
            marks[product_id] = mark
    return marks


def _performance_books(ledger: DeploymentLedger) -> tuple[PerformanceBookPayload, ...]:
    """Render per-product ledger slices for operator performance evidence."""
    if not ledger.books:
        return ()
    return tuple(
        PerformanceBookPayload(
            product_id=book.product_id,
            trade_count=book.trade_count,
            total_net_pnl=(None if book.total_net_pnl is None else format(book.total_net_pnl, "f")),
            mark_complete=book.mark_complete,
        )
        for book in ledger.books
    )


def _runtime_fee_treatment(deployment: Deployment) -> str:
    """Describe how paper versus live fees enter the fill ledger."""
    if deployment.mode is DeploymentMode.PAPER:
        maker, taker = effective_paper_fee_rates(
            deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
        )
        return (
            f"documented paper assumptions: {format(maker, 'f')} maker / "
            f"{format(taker, 'f')} taker on recorded fills; not observed Coinbase "
            "fees; last-close mark for open inventory"
        )
    return (
        "venue fees recorded on fills; last-close mark for open inventory; "
        "live fee API is not queried here"
    )


def _deployment_ledger_component(
    deployment: Deployment, ledger: DeploymentLedger
) -> tuple[ComponentReport, tuple[str, ...]]:
    """Classify fill-ledger completeness without inventing missing marks or fills."""
    if not ledger.accounting_complete:
        return (
            ComponentReport(
                name="performance",
                status=ReportStatus.DEGRADED,
                reason_code="ACCOUNTING_UNRESOLVED",
                detail=(
                    "Inventory projection, fill economics, or accounting scope is unresolved; "
                    "aggregate PnL, equity and exposure are omitted, not reconstructed."
                ),
            ),
            ("Recorded fill statistics do not certify complete account economics.",),
        )
    if not ledger.mark_complete:
        return (
            ComponentReport(
                name="performance",
                status=ReportStatus.DEGRADED,
                reason_code="MISSING_MARK",
                detail=(
                    "Open inventory has no last-close mark; total PnL is omitted "
                    "rather than invented."
                ),
            ),
            ("Open inventory is unmarked; total PnL is omitted rather than invented.",),
        )
    if deployment.mismatch_detail:
        return (
            ComponentReport(
                name="performance",
                status=ReportStatus.DEGRADED,
                reason_code="STATE_MISMATCH",
                detail=deployment.mismatch_detail[:500],
            ),
            ("Pause or mismatch stays operator risk; PnL uses recorded fills and the last close.",),
        )
    if deployment.status is DeploymentStatus.PAUSED:
        return (
            ComponentReport(
                name="performance",
                status=ReportStatus.DEGRADED,
                reason_code="DEPLOYMENT_PAUSED",
                detail="The deployment is paused; PnL still reflects recorded fills.",
            ),
            ("The deployment is paused; PnL still reflects recorded fills.",),
        )
    return (
        ComponentReport(
            name="performance",
            status=ReportStatus.HEALTHY,
            reason_code="FILL_LEDGER",
            detail="Metrics are a fill ledger marked at the last closed candle close.",
        ),
        (
            "Maximum drawdown walks fill-event marks plus the current last close; "
            "it is not a bar equity curve.",
        ),
    )


def _empty_performance(now: datetime, components: tuple[ComponentReport, ...]) -> PerformanceReport:
    """Return a failed or empty performance report with a placeholder payload."""
    payload = PerformancePayload(
        mode="backtest",
        timeframe="1h",
        strategy_fingerprint=None,
        dataset_fingerprint=None,
        fee_treatment="unspecified",
        result_fingerprint=None,
        deployment_id=None,
        trade_count=None,
        total_net_pnl=None,
        total_return_fraction=None,
        maximum_drawdown_fraction=None,
        total_spread_cost=None,
        evaluation_bars=None,
    )
    return PerformanceReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=components,
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )
