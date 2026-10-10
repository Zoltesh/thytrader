"""Operator ``products --kind future|all``: the read-only futures listing (ADR 0126).

The default ``products`` report (spot) is built by ``market_coverage`` and is unchanged;
this module only adds the futures listing beside it. A futures catalog that is not
configured (demo mode) or cannot be read is reported as such, never as an empty listing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader import __version__
from thytrader.data_control.service import ingestion_provider
from thytrader.market_data.instruments import InstrumentKind
from thytrader.operator.diagnostics.market_coverage import build_products_report
from thytrader.operator.market_models import (
    FuturesProductSummary,
    ProductsPayload,
    ProductsReport,
)
from thytrader.operator.models import STANDARD_REDACTION, ComponentReport, ReportStatus
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from thytrader.market_data.instruments import FuturesCatalogSnapshot, FuturesProduct
    from thytrader.operator.service import OperatorDiagnostics


async def build_futures_products_report(
    diagnostics: OperatorDiagnostics, kind: Literal["future", "all"]
) -> ProductsReport:
    """List futures contracts, plus the unchanged spot catalog when ``kind`` is ``all``."""
    now = datetime.now(UTC)
    if kind == "all":
        spot = await build_products_report(diagnostics)
        payload = spot.payload
        components: tuple[ComponentReport, ...] = spot.components
    else:
        payload = ProductsPayload(provider=ingestion_provider(diagnostics.settings), products=())
        components = ()
    futures_component, snapshot = await _futures_snapshot(diagnostics)
    components = (*components, futures_component)
    payload = payload.model_copy(
        update={
            "kind": kind,
            "futures": (() if snapshot is None else tuple(_summary(p) for p in snapshot.products)),
            "futures_catalog_fingerprint": None if snapshot is None else snapshot.fingerprint,
            "futures_catalog_observed_at": None if snapshot is None else snapshot.observed_at,
        }
    )
    return ProductsReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=components,
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )


async def _futures_snapshot(
    diagnostics: OperatorDiagnostics,
) -> tuple[ComponentReport, FuturesCatalogSnapshot | None]:
    """Read the cached futures listing, or say why it is unavailable."""
    cache = None if diagnostics.market_data is None else diagnostics.market_data.futures_catalog
    if cache is None:
        return (
            ComponentReport(
                name="futures_products",
                status=ReportStatus.DEGRADED,
                reason_code="FUTURES_CATALOG_UNCONFIGURED",
                detail="No futures listing is configured (demo mode: no Coinbase credentials).",
            ),
            None,
        )
    try:
        snapshot = await cache.snapshot()
    except Exception:  # noqa: BLE001 - listing failures stay redacted at this boundary.
        return (
            ComponentReport(
                name="futures_products",
                status=ReportStatus.FAILED,
                reason_code="FUTURES_CATALOG_UNAVAILABLE",
                detail=(
                    "The futures listing could not be read or proved complete; nothing is "
                    "inferred. Retry the same command."
                ),
            ),
            None,
        )
    perps = sum(p.kind is InstrumentKind.PERPETUAL_FUTURE for p in snapshot.products)
    return (
        ComponentReport(
            name="futures_products",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=(
                f"{len(snapshot.products)} futures contract(s), {perps} perpetual-style; "
                "read-only, not orderable."
            ),
        ),
        snapshot,
    )


def _summary(product: FuturesProduct) -> FuturesProductSummary:
    """Project one parsed contract onto the operator summary with exact strings."""
    intraday = product.intraday_margin
    overnight = product.overnight_margin
    funding = product.funding
    maintenance = product.session.maintenance
    return FuturesProductSummary(
        product_id=product.product_id,
        kind=(
            "perpetual_future"
            if product.kind is InstrumentKind.PERPETUAL_FUTURE
            else "dated_future"
        ),
        contract_code=product.contract_code,
        underlying=product.underlying,
        settlement_currency="USD",
        contract_size=format(product.contract_size, "f"),
        price_increment=format(product.price_increment, "f"),
        base_increment=format(product.base_increment, "f"),
        base_min_size=format(product.base_min_size, "f"),
        expires_at=product.expires_at,
        venue_expiry_at=product.venue_expiry_at,
        listed_expiry=product.listed_expiry,
        twenty_four_by_seven=product.twenty_four_by_seven,
        trading_enabled=product.trading_enabled,
        intraday_long_margin_rate=None if intraday is None else format(intraday.long, "f"),
        intraday_short_margin_rate=None if intraday is None else format(intraday.short, "f"),
        overnight_long_margin_rate=None if overnight is None else format(overnight.long, "f"),
        overnight_short_margin_rate=None if overnight is None else format(overnight.short, "f"),
        funding_interval_seconds=(
            None if funding is None else int(funding.interval.total_seconds())
        ),
        funding_rate=None if funding is None or funding.rate is None else format(funding.rate, "f"),
        funding_time=None if funding is None else funding.funding_time,
        session_open=product.session.is_open,
        session_state=product.session.state,
        maintenance_starts_at=None if maintenance is None else maintenance.starts_at,
        maintenance_ends_at=None if maintenance is None else maintenance.ends_at,
        asset_type=product.asset_type,
        display_name=product.display_name,
    )
