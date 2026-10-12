"""Backtest and paper/live deployment performance operator reports."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import (
    Clamped,
    Decimal,
    DecimalException,
    Inexact,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)
from typing import TYPE_CHECKING

from thytrader import __version__
from thytrader.backtest.cost_attribution import compute_cost_attribution
from thytrader.backtest.metrics import compute_performance_metrics
from thytrader.backtest.models import (
    BacktestEvaluationWindow,
    BacktestResult,
    backtest_evaluation_window,
)
from thytrader.backtest.results import (
    BacktestResultNotFoundError,
    BacktestResultUnavailableError,
    BacktestSourceSpecificationReader,
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
    ReportStatus,
    SupportedTimeframe,
)
from thytrader.operator.runtime_models import (
    PerformanceBookPayload,
    PerformancePayload,
    PerformanceReport,
    PerformanceValuationReceipt,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.trading.ledger import (
    effective_paper_fee_rates,
    ledger_fills_for_product,
    ledger_from_snapshot,
    mark_deployment_ledger,
)
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.trading.protection import book_inventory_reasons

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
    marks, receipts = await _deployment_marks(diagnostics, snapshot, timeframe)
    ledger = ledger_from_snapshot(snapshot, marks=marks)
    recorded = _recorded_book_ledgers(snapshot)
    books = _performance_books(ledger, snapshot, receipts, recorded)
    component, warnings = _deployment_ledger_component(deployment, ledger)
    warnings = (
        *warnings,
        *(
            f"{book.product_id}: {book.valuation.reason_code}; "
            "preview price not used for valuation."
            for book in books
            if not book.valuation.used and book.valuation.status != "not_required"
        ),
    )
    if currency_warning is not None:
        warnings = (*warnings, currency_warning)
    if all(book.valuation.status == "not_required" for book in books) and any(
        book.total_net_pnl is None for book in books
    ):
        warnings = (
            *warnings,
            "Historical book totals are omitted: recorded fill realized PnL does not prove "
            "a partition of deployment total PnL; non-fill cash such as applied funding "
            "can differ. Recorded fill statistics exclude these cash flows.",
        )
    payload = PerformancePayload(
        mode="live" if deployment.mode is DeploymentMode.LIVE else "paper",
        timeframe=timeframe,
        currency=currency,
        strategy_fingerprint=deployment.strategy_fingerprint,
        dataset_fingerprint=None,
        fee_treatment=_runtime_fee_treatment(deployment),
        result_fingerprint=None,
        deployment_id=deployment.id,
        trade_count=sum(book.trade_count for book in recorded.values()),
        total_net_pnl=ledger.total_net_pnl_text(),
        total_return_fraction=ledger.total_return_fraction_text(),
        maximum_drawdown_fraction=ledger.maximum_drawdown_fraction_text(),
        total_spread_cost=None,
        evaluation_bars=None,
        mark_complete=ledger.mark_complete,
        marked_exposure=(
            None if ledger.marked_exposure is None else format(ledger.marked_exposure, "f")
        ),
        realized_net_pnl=format(
            sum((book.realized_net_pnl for book in recorded.values()), Decimal(0)), "f"
        ),
        total_fees=format(sum((book.total_fees for book in recorded.values()), Decimal(0)), "f"),
        books=books,
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
) -> PerformanceValuationReceipt:
    """Reobserve preview evidence without promoting a stale candidate to a mark."""
    unavailable = PerformanceValuationReceipt(
        status="unavailable", reason_code="PREVIEW_UNAVAILABLE"
    )
    if diagnostics.market_data is None:
        return unavailable
    try:
        preview = await diagnostics.market_data.get_preview(
            product_id, parse_candle_interval(timeframe)
        )
    except Exception:  # noqa: BLE001 - missing marks stay omitted, not invented.
        return unavailable
    candles = preview.quality.candles
    if not candles:
        return PerformanceValuationReceipt(
            status="empty", observed_at=preview.as_of, reason_code="EMPTY_PREVIEW"
        )
    stale = preview.quality.is_stale
    return PerformanceValuationReceipt(
        status="stale" if stale else "fresh",
        price=format(candles[-1].close, "f"),
        candle_closed_at=candles[-1].starts_at + preview.interval.duration,
        observed_at=preview.as_of,
        reason_code="STALE_MARK" if stale else "FRESH_MARK",
    )


async def _deployment_marks(
    diagnostics: OperatorDiagnostics,
    snapshot: DeploymentSnapshot,
    timeframe: SupportedTimeframe,
) -> tuple[dict[str, Decimal], dict[str, PerformanceValuationReceipt]]:
    """Observe open books only; return accepted marks separately from candidate receipts."""
    marks: dict[str, Decimal] = {}
    receipts: dict[str, PerformanceValuationReceipt] = {}
    products = {
        resolved_product_id(position.product_id, snapshot.deployment)
        for position in snapshot_positions(snapshot)
        if position.quantity != 0
    }
    for product_id in sorted(products):
        receipt = await _last_close_mark(diagnostics, product_id, timeframe)
        receipts[product_id] = receipt
        if receipt.status == "fresh" and receipt.price is not None:
            marks[product_id] = Decimal(receipt.price)
    return marks, receipts


def _recorded_book_ledgers(snapshot: DeploymentSnapshot) -> dict[str, DeploymentLedger]:
    """Fold each product independently for recorded statistics, never shared inventory.

    LedgerFill has no product identity. Combining products before folding would net
    unrelated lots. These zero-cash folds supply only recorded facts and flatness;
    current economics continue to come from ledger_from_snapshot unchanged.
    """
    products = {
        snapshot.deployment.product_id,
        *(
            resolved_product_id(position.product_id, snapshot.deployment)
            for position in snapshot_positions(snapshot)
        ),
        *(resolved_product_id(order.product_id, snapshot.deployment) for order in snapshot.orders),
        *(runtime.product_id for runtime in snapshot.instrument_runtimes),
    }
    return {
        product_id: mark_deployment_ledger(
            starting_cash=Decimal(0),
            cash=Decimal(0),
            fills=ledger_fills_for_product(snapshot, product_id),
            position_quantity=Decimal(0),
            position_entry_price=None,
            mark_price=None,
        )
        for product_id in sorted(products)
    }


def _performance_books(
    ledger: DeploymentLedger,
    snapshot: DeploymentSnapshot,
    receipts: dict[str, PerformanceValuationReceipt],
    recorded: dict[str, DeploymentLedger],
) -> tuple[PerformanceBookPayload, ...]:
    """Keep shared-cash open projections separate from product-local recorded facts."""
    books = {book.product_id: book for book in ledger.books}
    has_open_inventory = any(
        position.quantity != 0 for position in snapshot_positions(snapshot)
    ) or any(history.base_quantity != 0 for history in recorded.values())
    partition_complete = _recorded_totals_partition(ledger, recorded)
    result: list[PerformanceBookPayload] = []
    for product_id, history in recorded.items():
        book = books.get(product_id)
        receipt = receipts.get(
            product_id,
            PerformanceValuationReceipt(status="not_required", reason_code="NO_OPEN_INVENTORY"),
        )
        if book is None:
            result.append(
                _historical_performance_book(
                    snapshot,
                    product_id,
                    receipt,
                    history,
                    has_open_inventory=has_open_inventory,
                    partition_complete=partition_complete,
                )
            )
            continue
        used = receipt.status == "fresh" and book.mark_complete and book.base_quantity != 0
        receipt = receipt.model_copy(update={"used": used})
        if receipt.status == "fresh" and not used:
            receipt = receipt.model_copy(update={"reason_code": "ACCOUNTING_UNRESOLVED"})
        result.append(
            PerformanceBookPayload(
                product_id=book.product_id,
                trade_count=history.trade_count,
                realized_net_pnl=format(history.realized_net_pnl, "f"),
                total_fees=format(history.total_fees, "f"),
                total_net_pnl=(
                    None if book.total_net_pnl is None else format(book.total_net_pnl, "f")
                ),
                mark_complete=book.mark_complete,
                valuation=receipt,
            )
        )
    return tuple(result)


def _recorded_totals_partition(
    ledger: DeploymentLedger, recorded: dict[str, DeploymentLedger]
) -> bool:
    """Prove fill-only historical totals match cash economics without rounded equality."""
    if ledger.total_net_pnl is None or not ledger.total_net_pnl.is_finite():
        return False
    try:
        with localcontext() as context:
            for signal in (Clamped, Inexact, InvalidOperation, Overflow, Underflow):
                context.traps[signal] = True
            total = sum((book.realized_net_pnl for book in recorded.values()), Decimal(0))
            return total.is_finite() and total == ledger.total_net_pnl
    except DecimalException:
        return False


def _historical_performance_book(
    snapshot: DeploymentSnapshot,
    product_id: str,
    receipt: PerformanceValuationReceipt,
    history: DeploymentLedger,
    *,
    has_open_inventory: bool,
    partition_complete: bool,
) -> PerformanceBookPayload:
    """Expose closed trades without duplicating profit embedded in open-book shared cash."""
    complete = (
        snapshot.accounting_complete
        and history.mark_complete
        and not book_inventory_reasons(snapshot, product_id=product_id)
    )
    if not complete:
        receipt = PerformanceValuationReceipt(
            status="unavailable", reason_code="ACCOUNTING_UNRESOLVED"
        )
    return PerformanceBookPayload(
        product_id=product_id,
        trade_count=history.trade_count,
        realized_net_pnl=format(history.realized_net_pnl, "f"),
        total_fees=format(history.total_fees, "f"),
        total_net_pnl=(
            format(history.realized_net_pnl, "f")
            if complete and not has_open_inventory and partition_complete
            else None
        ),
        mark_complete=complete,
        valuation=receipt,
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
            "fees; accepted fresh preview close required to mark open inventory"
        )
    return (
        "venue fees recorded on fills; accepted fresh preview close required to mark open "
        "inventory; live fee API is not queried here"
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
                    "Open inventory has no accepted fresh last-close mark (stale or missing); "
                    "total PnL is omitted "
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
