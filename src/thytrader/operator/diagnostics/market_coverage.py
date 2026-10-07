"""Market-data freshness, product catalog, and dataset coverage operator reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader import __version__
from thytrader.data_control.service import ingestion_provider
from thytrader.market_data.freshness import (
    FreshnessStatus,
    evaluate_freshness,
    freshest_bar_start,
)
from thytrader.market_data.models import CandleInterval, as_dataset_timeframe, parse_candle_interval
from thytrader.market_data.watch_coverage import (
    island_covers_watch,
    watch_covered_candle_count,
    watch_expected_candle_count,
)
from thytrader.market_data.watchlist import (
    MarketDataWatchlistUnavailableError,
    MarketDataWatchTarget,
)
from thytrader.market_data.worker_state import (
    MarketDataWorkerState,
    MarketDataWorkerUnavailableError,
)
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    DataCatalogPayload,
    DataCatalogReport,
    DatasetCoverageRow,
    MarketDataPayload,
    MarketDataReport,
    ProductsPayload,
    ProductsReport,
    ProductSummary,
    ReportStatus,
)
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from thytrader.operator.service import OperatorDiagnostics


async def build_market_data_report(
    diagnostics: OperatorDiagnostics,
    product_id: str | None = None,
    timeframe: str | None = None,
) -> MarketDataReport:
    """Report coverage and freshness for one USD spot product and timeframe."""
    now = datetime.now(UTC)
    target = product_id or diagnostics.settings.market_data_worker_product_id
    interval = _parse_timeframe(timeframe)
    component, payload, warnings = await _market_data_snapshot(diagnostics, target, now, interval)
    components = [component]
    return MarketDataReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )


async def build_products_report(diagnostics: OperatorDiagnostics) -> ProductsReport:
    """List enabled USD spot products from the current catalog."""
    now = datetime.now(UTC)
    if diagnostics.market_data is None:
        component = ComponentReport(
            name="products",
            status=ReportStatus.DEGRADED,
            reason_code="CATALOG_UNAVAILABLE",
            detail="Market-data service is not attached to diagnostics.",
        )
        return ProductsReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.DEGRADED,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=ProductsPayload(provider="unknown", products=()),
        )
    try:
        catalog = await diagnostics.market_data.catalog_snapshot()
        listed = catalog.enabled_products
    except Exception:  # noqa: BLE001 - catalog failures stay redacted.
        component = ComponentReport(
            name="products",
            status=ReportStatus.FAILED,
            reason_code="CATALOG_UNAVAILABLE",
            detail="The USD spot product catalog could not be loaded.",
        )
        return ProductsReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.FAILED,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=ProductsPayload(provider=ingestion_provider(diagnostics.settings), products=()),
        )
    component = ComponentReport(
        name="products",
        status=ReportStatus.HEALTHY,
        reason_code="OK",
        detail=f"{len(listed)} enabled USD spot product(s).",
    )
    return ProductsReport(
        application_version=__version__,
        generated_at=now,
        overall_status=ReportStatus.HEALTHY,
        components=(component,),
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action((component,)),
        payload=ProductsPayload(
            provider=ingestion_provider(diagnostics.settings),
            catalog_fingerprint=catalog.fingerprint,
            catalog_observed_at=catalog.observed_at,
            products=tuple(
                ProductSummary(
                    product_id=item.product_id,
                    base_currency=item.base_currency,
                    quote_currency=item.quote_currency,
                    trading_enabled=item.trading_enabled,
                    status=item.status,
                    alias=item.alias,
                    price_increment=format(item.price_increment, "f"),
                    base_increment=format(item.base_increment, "f"),
                    quote_increment=format(item.quote_increment, "f"),
                    base_min_size=format(item.base_min_size, "f"),
                    quote_min_size=format(item.quote_min_size, "f"),
                )
                for item in listed
            ),
        ),
    )


async def build_data_catalog_report(diagnostics: OperatorDiagnostics) -> DataCatalogReport:
    """Join watchlist, worker state, and verified Parquet datasets."""
    now = datetime.now(UTC)
    warnings: list[str] = []
    components: list[ComponentReport] = []
    rows = await _coverage_rows(diagnostics, now, components, warnings)
    if not components:
        components.append(
            ComponentReport(
                name="data_catalog",
                status=ReportStatus.HEALTHY,
                reason_code="OK",
                detail=f"{len(rows)} coverage row(s).",
            )
        )
    return DataCatalogReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=DataCatalogPayload(datasets=rows),
    )


async def _market_data_snapshot(
    diagnostics: OperatorDiagnostics,
    product_id: str,
    now: datetime,
    interval: CandleInterval,
) -> tuple[ComponentReport, MarketDataPayload, list[str]]:
    """Load durable worker state for demo or live provenance."""
    warnings: list[str] = []
    try:
        state, provider = await _load_worker_state(diagnostics, product_id, interval)
    except MarketDataWorkerUnavailableError:
        payload = _empty_market_data(product_id, FreshnessStatus.UNKNOWN, interval)
        return (
            ComponentReport(
                name="market_data",
                status=ReportStatus.FAILED,
                reason_code="MARKET_DATA_STATE_UNAVAILABLE",
                detail="Durable market-data worker state could not be read.",
            ),
            payload,
            warnings,
        )
    if state is None:
        payload = _empty_market_data(product_id, FreshnessStatus.UNKNOWN, interval)
        return (
            ComponentReport(
                name="market_data",
                status=ReportStatus.DEGRADED,
                reason_code="MARKET_DATA_NEVER_RUN",
                detail=(f"No verified {interval.value} coverage exists for this product."),
            ),
            payload,
            warnings,
        )
    freshness = evaluate_freshness(
        product_id=product_id,
        newest_candle_at=freshest_bar_start(state.covered_ends_at, interval),
        now=now,
        interval=interval,
    )
    payload = MarketDataPayload(
        product_id=product_id,
        provider=provider,
        timeframe=as_dataset_timeframe(interval),
        worker_status=state.status.value,
        complete=state.complete,
        freshness_status=freshness.status.value,
        newest_candle_at=freshness.newest_candle_at,
        age_seconds=freshness.age_seconds,
        gap_count=state.gap_count,
        missing_intervals=state.missing_intervals,
        expected_candle_count=state.expected_candle_count,
        received_candle_count=state.received_candle_count,
        content_fingerprint=state.content_fingerprint,
        covered_starts_at=state.covered_starts_at,
        covered_ends_at=state.covered_ends_at,
    )
    return _market_data_component(state, freshness, interval), payload, warnings


async def _load_worker_state(
    diagnostics: OperatorDiagnostics,
    product_id: str,
    interval: CandleInterval,
) -> tuple[MarketDataWorkerState | None, str | None]:
    """Prefer live Coinbase state, then demo, without inventing coverage."""
    last_unavailable = False
    for provider in ("coinbase", "demo"):
        try:
            state = await diagnostics.market_data_state.get(
                provider,
                product_id,
                interval,
            )
        except MarketDataWorkerUnavailableError:
            last_unavailable = True
            continue
        if state is not None:
            return state, provider
    if last_unavailable:
        raise MarketDataWorkerUnavailableError("Market-data worker state is unavailable.")
    return None, None


async def _coverage_rows(
    diagnostics: OperatorDiagnostics,
    now: datetime,
    components: list[ComponentReport],
    warnings: list[str],
) -> tuple[DatasetCoverageRow, ...]:
    """Build one catalog row per watch, worker, or verified dataset identity."""
    watched: tuple[MarketDataWatchTarget, ...] = ()
    if diagnostics.watchlist is not None:
        try:
            watched = await diagnostics.watchlist.list_all()
        except MarketDataWatchlistUnavailableError:
            warnings.append("Watchlist is unavailable; catalog omits watch flags.")
            components.append(
                ComponentReport(
                    name="watchlist",
                    status=ReportStatus.DEGRADED,
                    reason_code="WATCHLIST_UNAVAILABLE",
                    detail="The ingestion watchlist could not be read.",
                )
            )
    worker_states: tuple[MarketDataWorkerState, ...] = ()
    try:
        worker_states = await diagnostics.market_data_state.list_all()
    except MarketDataWorkerUnavailableError:
        warnings.append("Worker state is unavailable; catalog omits ingestion status.")
        components.append(
            ComponentReport(
                name="market_data",
                status=ReportStatus.DEGRADED,
                reason_code="MARKET_DATA_STATE_UNAVAILABLE",
                detail="Durable market-data worker state could not be listed.",
            )
        )
    manifests = (
        ()
        if diagnostics.dataset_store is None
        else diagnostics.dataset_store.list_latest_verified()
    )
    return _merge_coverage_rows(now, watched, worker_states, manifests)


def _empty_market_data(
    product_id: str,
    freshness: FreshnessStatus,
    interval: CandleInterval,
) -> MarketDataPayload:
    """Build an empty market-data payload when coverage is missing."""
    return MarketDataPayload(
        product_id=product_id,
        provider=None,
        timeframe=as_dataset_timeframe(interval),
        worker_status=None,
        complete=None,
        freshness_status=freshness.value,
        newest_candle_at=None,
        age_seconds=None,
        gap_count=None,
        missing_intervals=None,
        expected_candle_count=None,
        received_candle_count=None,
        content_fingerprint=None,
        covered_starts_at=None,
        covered_ends_at=None,
    )


def _market_data_component(
    state: MarketDataWorkerState,
    freshness: object,
    interval: CandleInterval,
) -> ComponentReport:
    """Classify coverage completeness and candle freshness."""
    status_name = getattr(freshness, "status", FreshnessStatus.UNKNOWN)
    if not state.complete or (state.gap_count or 0) > 0 or (state.missing_intervals or 0) > 0:
        return ComponentReport(
            name="market_data",
            status=ReportStatus.DEGRADED,
            reason_code="GAPS_PRESENT",
            detail="Verified coverage is incomplete or contains gaps.",
        )
    if status_name is FreshnessStatus.STALE:
        return ComponentReport(
            name="market_data",
            status=ReportStatus.DEGRADED,
            reason_code="STALE",
            detail=(
                "The newest verified candle is older than the "
                f"{interval.value} freshness threshold."
            ),
        )
    if status_name is FreshnessStatus.UNKNOWN:
        return ComponentReport(
            name="market_data",
            status=ReportStatus.DEGRADED,
            reason_code="UNKNOWN",
            detail="Freshness could not be evaluated.",
        )
    return ComponentReport(
        name="market_data",
        status=ReportStatus.HEALTHY,
        reason_code="FRESH",
        detail=f"Verified {interval.value} coverage is complete and fresh.",
    )


def _parse_timeframe(value: str | None) -> CandleInterval:
    """Default operator market-data reports to 1h when unspecified."""
    if value is None or value == "":
        return CandleInterval.ONE_HOUR
    try:
        return parse_candle_interval(value)
    except ValueError:
        return CandleInterval.ONE_HOUR


def _merge_coverage_rows(
    now: datetime,
    watched: tuple[MarketDataWatchTarget, ...],
    states: tuple[MarketDataWorkerState, ...],
    manifests: tuple[object, ...],
) -> tuple[DatasetCoverageRow, ...]:
    """Join watchlist, worker, and dataset identities into catalog rows."""
    watch_index = {(item.provider, item.product_id, item.timeframe.value): item for item in watched}
    state_index = {(item.provider, item.product_id, item.timeframe.value): item for item in states}
    manifest_index: dict[tuple[str, str, str], object] = {}
    for manifest in manifests:
        provider = getattr(manifest, "provider", None)
        product_id = getattr(manifest, "product_id", None)
        timeframe = getattr(manifest, "timeframe", None)
        if (
            isinstance(provider, str)
            and isinstance(product_id, str)
            and isinstance(timeframe, str)
            and _supported_timeframe_token(timeframe) is not None
        ):
            manifest_index[(provider, product_id, timeframe)] = manifest
    keys = sorted({*watch_index, *state_index, *manifest_index})
    return tuple(
        _coverage_row(
            key,
            now,
            watch_index.get(key),
            state_index.get(key),
            manifest_index.get(key),
        )
        for key in keys
    )


def _supported_timeframe_token(value: str) -> str | None:
    """Return a dataset timeframe token, otherwise omit the catalog row."""
    try:
        return parse_candle_interval(value).value
    except ValueError:
        return None


def _coverage_row(
    key: tuple[str, str, str],
    now: datetime,
    watched: MarketDataWatchTarget | None,
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> DatasetCoverageRow:
    """Build one catalog row from optional watch, worker, and dataset facts."""
    provider, product_id, timeframe = key
    interval = parse_candle_interval(timeframe)
    newest = state.covered_ends_at if state is not None else _manifest_end(manifest)
    freshness = evaluate_freshness(
        product_id=product_id,
        newest_candle_at=freshest_bar_start(newest, interval),
        now=now,
        interval=interval,
    )
    island_complete = _coverage_complete(state, manifest)
    gap_count = state.gap_count if state is not None else _manifest_int(manifest, "gap_count")
    if state is not None:
        missing = state.missing_intervals
    else:
        missing = _manifest_int(manifest, "missing_intervals")
    lookback_hours = watched.lookback_hours if watched is not None else None
    closed_end = interval.align_closed_end(now)
    watch_expected = (
        watch_expected_candle_count(lookback_hours, interval, closed_end)
        if lookback_hours is not None
        else None
    )
    covered_start = _coverage_start(state, manifest)
    history_floor_at = state.history_floor_at if state is not None else None
    watch_complete = (
        island_covers_watch(
            covered_starts_at=covered_start,
            covered_ends_at=newest,
            island_complete=island_complete is True,
            lookback_hours=lookback_hours,
            interval=interval,
            closed_end=closed_end,
            product_id=product_id,
            now=now,
            history_floor_at=history_floor_at,
        )
        if lookback_hours is not None
        else None
    )
    island_sparsity = _coverage_sparsity(island_complete, gap_count, missing)
    watch_sparsity = (
        _watch_sparsity(watch_complete, island_sparsity) if lookback_hours is not None else None
    )
    watch_covered = (
        watch_covered_candle_count(covered_start, newest, lookback_hours, interval, closed_end)
        if lookback_hours is not None
        else None
    )
    return DatasetCoverageRow(
        provider=provider,
        product_id=product_id,
        timeframe=as_dataset_timeframe(interval),
        watched=watched is not None and watched.enabled,
        lookback_hours=lookback_hours,
        worker_status=state.status.value if state is not None else None,
        failure_code=state.failure_code if state is not None else None,
        failure_message=state.failure_message if state is not None else None,
        watch_complete=watch_complete,
        complete=_watch_relative_complete(island_complete, watch_complete),
        island_complete=island_complete,
        freshness_status=freshness.status.value,
        covered_starts_at=covered_start,
        covered_ends_at=newest,
        expected_candle_count=_coverage_expected(state, manifest),
        received_candle_count=_coverage_received(state, manifest),
        gap_count=gap_count,
        missing_intervals=missing,
        content_fingerprint=_coverage_fingerprint(state, manifest),
        sparsity=island_sparsity,
        watch_sparsity=watch_sparsity,
        watch_expected_candle_count=watch_expected,
        watch_covered_candle_count=watch_covered,
        watch_coverage_ratio=_coverage_ratio(watch_covered, watch_expected),
        synthetic_no_trade_intervals=_manifest_int(manifest, "synthetic_no_trade_intervals"),
        watch_status=_watch_status(watch_complete),
        history_floor_at=history_floor_at,
    )


def _watch_relative_complete(
    island_complete: bool | None, watch_complete: bool | None
) -> bool | None:
    """Return catalog completeness: a watched target is complete only across its lookback.

    A two-minute island for a 90-day watch is not complete (ADR 0095). Unwatched rows
    keep island completeness.
    """
    if watch_complete is None:
        return island_complete
    return island_complete is True and watch_complete


def _coverage_ratio(covered: int | None, expected: int | None) -> float | None:
    """Return covered over expected watch bars, rounded to four places, when both are known."""
    if covered is None or not expected:
        return None
    return round(covered / expected, 4)


def _watch_status(watch_complete: bool | None) -> Literal["complete", "backfilling", "unknown"]:
    """Restate watch_complete as an operator noun; succeeded means latest chunk only."""
    if watch_complete is True:
        return "complete"
    if watch_complete is False:
        return "backfilling"
    return "unknown"


def _coverage_sparsity(
    complete: bool | None,
    gap_count: int | None,
    missing: int | None,
) -> Literal["none", "unknown", "gapped"]:
    """Classify island holes without interpolating prices."""
    if complete and not gap_count and not missing:
        return "none"
    if gap_count or missing or complete is False:
        return "gapped"
    return "unknown"


def _watch_sparsity(
    watch_complete: bool | None,
    island_sparsity: Literal["none", "unknown", "gapped"],
) -> Literal["none", "unknown", "gapped"]:
    """Classify configured-watch holes separately from island completeness."""
    if watch_complete is True:
        return "none"
    if watch_complete is False:
        return "gapped"
    return island_sparsity


def _coverage_complete(state: MarketDataWorkerState | None, manifest: object | None) -> bool | None:
    """Prefer worker completeness, then a verified manifest."""
    if state is not None:
        return state.complete
    if manifest is not None:
        return True
    return None


def _coverage_start(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> datetime | None:
    """Return covered range start from worker state or manifest text."""
    if state is not None:
        return state.covered_starts_at
    return _manifest_datetime(manifest, "starts_at")


def _coverage_expected(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> int | None:
    """Return expected candle count from worker state or manifest."""
    if state is not None:
        return state.expected_candle_count
    return _manifest_int(manifest, "expected_candle_count")


def _coverage_received(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> int | None:
    """Return received candle count from worker state or manifest."""
    if state is not None:
        return state.received_candle_count
    return _manifest_int(manifest, "received_candle_count")


def _coverage_fingerprint(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> str | None:
    """Return the verified dataset fingerprint when present."""
    if state is not None:
        return state.content_fingerprint
    value = getattr(manifest, "content_fingerprint", None)
    return value if isinstance(value, str) else None


def _manifest_end(manifest: object | None) -> datetime | None:
    """Parse a manifest exclusive end instant."""
    return _manifest_datetime(manifest, "ends_at")


def _manifest_int(manifest: object | None, field: str) -> int | None:
    """Read one optional integer manifest field."""
    value = getattr(manifest, field, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _manifest_datetime(manifest: object | None, field: str) -> datetime | None:
    """Parse one optional UTC timestamp stored as ISO-8601 text."""
    value = getattr(manifest, field, None)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
