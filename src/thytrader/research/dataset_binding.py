"""Bind omitted research datasets to the newest complete catalog revision (ADR 0089).

Backtest and study starts may omit dataset fingerprints. For every product and
clock the strategy needs (decision clock, HTF filter, extra indicator clocks, each
additional instrument, and each read-only reference instrument of ADR 0096), the
server binds the newest catalog-verified complete dataset from the configured
ingestion provider. Explicit fingerprints are used
unchanged. Every binding is echoed back as a :class:`BoundDataset`, and the bound
fingerprints become part of the run's identity, so results stay exactly
reproducible. A clock with no cataloged dataset fails closed with
:class:`DatasetsMissingError`, which names the ``thytrader-data`` commands that
would create it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from thytrader.research.models import (
    AdditionalInstrumentDataset,
    IndicatorTimeframeDataset,
    ReferenceInstrumentDataset,
)
from thytrader.strategies.models import (
    lockstep_product_ids,
    reference_instruments,
    unbound_indicator_timeframes,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from thytrader.backtest.submission import BacktestStartRequest
    from thytrader.market_data.datasets import DatasetManifest, DatasetStore
    from thytrader.strategies.models import StrategyDefinition

DatasetRole = Literal["decision", "filter", "indicator", "reference"]
_ROLE_LABELS: dict[DatasetRole, str] = {
    "decision": "decision clock",
    "filter": "HTF filter",
    "indicator": "indicator clock",
    "reference": "reference instrument",
}


class BoundDataset(BaseModel):
    """One dataset a research start used, echoed so the binding stays auditable.

    ``source`` is ``request`` for a fingerprint the caller sent and
    ``latest_catalog`` for one the server bound because the caller omitted it.
    ``reference_id`` names the reference instrument a ``reference`` row binds
    (ADR 0096); it is omitted for every other role.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str
    timeframe: str
    role: DatasetRole
    dataset_fingerprint: str
    source: Literal["request", "latest_catalog"]
    reference_id: str | None = Field(default=None, exclude_if=lambda value: value is None)


@dataclass(frozen=True, slots=True)
class DatasetNeed:
    """One product clock a strategy needs a dataset for (``reference_id`` for references)."""

    product_id: str
    timeframe: str
    role: DatasetRole
    reference_id: str | None = None


class DatasetsMissingError(ValueError):
    """No complete dataset is cataloged for one or more required clocks (HTTP 422).

    ``missing`` lists each unbound product clock so API callers can render it.
    """

    def __init__(self, missing: tuple[DatasetNeed, ...], *, provider: str) -> None:
        """Store the missing clocks and build the actionable message."""
        self.missing = missing
        self.provider = provider
        super().__init__(_missing_message(missing, provider))


def _missing_message(missing: tuple[DatasetNeed, ...], provider: str) -> str:
    """Name each missing clock and the commands that would create its dataset."""
    labels = ", ".join(
        f"{need.product_id} {need.timeframe} ({_ROLE_LABELS[need.role]})" for need in missing
    )
    commands = "; ".join(
        f"`uv run thytrader-data watch-add --product-id {need.product_id} --timeframe "
        f"{need.timeframe} --confirm` then `uv run thytrader-data ingest --product-id "
        f"{need.product_id} --timeframe {need.timeframe} --confirm`"
        for need in missing
    )
    return (
        f"No complete {provider} dataset is cataloged for {labels}. Watch and ingest each, "
        f"then retry: {commands}. Or pass explicit fingerprints from "
        "`uv run thytrader-operator data-catalog`."
    )


class LatestDatasetCatalog:
    """Newest catalog-verified complete dataset per product and clock for one provider."""

    def __init__(self, manifests: Iterable[DatasetManifest], *, provider: str) -> None:
        """Index the newest revision of each provider/product/timeframe listing."""
        self._fingerprints: dict[tuple[str, str], str] = {
            (manifest.product_id, manifest.timeframe): manifest.content_fingerprint
            for manifest in manifests
            if manifest.provider == provider and manifest.complete
        }

    def fingerprint(self, product_id: str, timeframe: str) -> str | None:
        """Return the newest complete dataset for one product clock, if cataloged."""
        return self._fingerprints.get((product_id, timeframe))


@dataclass(slots=True)
class DatasetResolver:
    """Resolve omitted fingerprints and record every binding for the response echo.

    The catalog is listed lazily, on the first omitted fingerprint, so a request with
    only explicit fingerprints never scans the dataset root.
    """

    store: DatasetStore | None
    provider: str
    bound: list[BoundDataset] = field(default_factory=list)
    missing: list[DatasetNeed] = field(default_factory=list)
    _catalog: LatestDatasetCatalog | None = None

    def resolve(self, need: DatasetNeed, explicit: str | None) -> str | None:
        """Return ``explicit`` or the newest cataloged dataset; record misses."""
        if explicit is not None:
            self._record(need, explicit, source="request")
            return explicit
        fingerprint = self._latest().fingerprint(need.product_id, need.timeframe)
        if fingerprint is None:
            if need not in self.missing:
                self.missing.append(need)
            return None
        self._record(need, fingerprint, source="latest_catalog")
        return fingerprint

    def require_complete(self) -> None:
        """Fail closed when any required clock had no cataloged dataset."""
        if self.missing:
            raise DatasetsMissingError(tuple(self.missing), provider=self.provider)

    def bindings(self) -> tuple[BoundDataset, ...]:
        """Return every binding in first-use order."""
        return tuple(self.bound)

    def _record(
        self, need: DatasetNeed, fingerprint: str, *, source: Literal["request", "latest_catalog"]
    ) -> None:
        """Remember one binding once, even when several children share it."""
        binding = BoundDataset(
            product_id=need.product_id,
            timeframe=need.timeframe,
            role=need.role,
            dataset_fingerprint=fingerprint,
            source=source,
            reference_id=need.reference_id,
        )
        if binding not in self.bound:
            self.bound.append(binding)

    def _latest(self) -> LatestDatasetCatalog:
        """List the catalog once per request."""
        if self._catalog is None:
            manifests = () if self.store is None else self.store.list_latest_verified()
            self._catalog = LatestDatasetCatalog(manifests, provider=self.provider)
        return self._catalog


@dataclass(frozen=True, slots=True)
class ClockBindings:
    """Decision, HTF, and extra-clock fingerprints for one product (None when missing)."""

    dataset_fingerprint: str | None
    htf_dataset_fingerprint: str | None
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...]


def bind_product_clocks(
    definition: StrategyDefinition,
    product_id: str,
    resolver: DatasetResolver,
    *,
    dataset_fingerprint: str | None,
    htf_dataset_fingerprint: str | None,
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...],
) -> ClockBindings:
    """Bind every clock ``definition`` needs on ``product_id``, keeping explicit values.

    Omitted HTF and extra-clock bindings are filled only for clocks the strategy
    declares. Explicit bindings the strategy does not declare are kept as sent, so
    the submission's own validation still rejects them.
    """
    primary = resolver.resolve(
        DatasetNeed(product_id, definition.timeframe, "decision"), dataset_fingerprint
    )
    htf = htf_dataset_fingerprint
    if definition.htf_filter is not None:
        htf = resolver.resolve(
            DatasetNeed(product_id, definition.htf_filter.timeframe, "filter"),
            htf_dataset_fingerprint,
        )
    clocks = _bind_extra_clocks(definition, product_id, resolver, indicator_dataset_fingerprints)
    return ClockBindings(
        dataset_fingerprint=primary,
        htf_dataset_fingerprint=htf,
        indicator_dataset_fingerprints=clocks,
    )


def _bind_extra_clocks(
    definition: StrategyDefinition,
    product_id: str,
    resolver: DatasetResolver,
    explicit: tuple[IndicatorTimeframeDataset, ...],
) -> tuple[IndicatorTimeframeDataset, ...]:
    """Fill omitted extra indicator clocks in the strategy's required order."""
    required = unbound_indicator_timeframes(definition)
    given = {item.timeframe: item.dataset_fingerprint for item in explicit}
    if len(given) != len(explicit) or not set(given) <= set(required):
        for item in explicit:
            resolver.resolve(
                DatasetNeed(product_id, item.timeframe, "indicator"), item.dataset_fingerprint
            )
        return explicit
    bound: list[IndicatorTimeframeDataset] = []
    for timeframe in required:
        fingerprint = resolver.resolve(
            DatasetNeed(product_id, timeframe, "indicator"), given.get(timeframe)
        )
        if fingerprint is not None:
            bound.append(
                IndicatorTimeframeDataset.model_validate(
                    {"timeframe": timeframe, "dataset_fingerprint": fingerprint}
                )
            )
    return tuple(bound)


def bind_backtest_datasets(
    start: BacktestStartRequest,
    definition: StrategyDefinition,
    resolver: DatasetResolver,
) -> BacktestStartRequest:
    """Return ``start`` with every omitted dataset bound for ``definition``.

    Raises:
        DatasetsMissingError: When a required clock has no cataloged dataset.
    """
    product_id = definition.instrument.product_id
    clocks = bind_product_clocks(
        definition,
        product_id,
        resolver,
        dataset_fingerprint=start.dataset_fingerprint,
        htf_dataset_fingerprint=start.htf_dataset_fingerprint,
        indicator_dataset_fingerprints=start.indicator_dataset_fingerprints,
    )
    additional = _bind_additional_instruments(start, definition, resolver)
    references = bind_reference_datasets(
        definition, resolver, explicit=start.reference_dataset_fingerprints
    )
    resolver.require_complete()
    return start.model_copy(
        update={
            "dataset_fingerprint": clocks.dataset_fingerprint,
            "htf_dataset_fingerprint": clocks.htf_dataset_fingerprint,
            "indicator_dataset_fingerprints": clocks.indicator_dataset_fingerprints,
            "additional_instrument_datasets": additional,
            "reference_dataset_fingerprints": references,
        }
    )


def bind_reference_datasets(
    definition: StrategyDefinition,
    resolver: DatasetResolver,
    *,
    explicit: tuple[ReferenceInstrumentDataset, ...] = (),
) -> tuple[ReferenceInstrumentDataset, ...]:
    """Bind every declared reference instrument (ADR 0096), keeping explicit fingerprints.

    References are shared by every covered product and every study leg, so they bind
    once per strategy. A cross-market variant keeps the base document's references
    (BTC stays BTC), so each leg binds the same reference series. An explicit list that
    names undeclared ids, repeats an id, or disagrees with a declared product or
    timeframe is kept as sent so the submission's own validation rejects it.
    """
    declared = reference_instruments(definition)
    given = {item.reference_id: item for item in explicit}
    by_id = {reference.id: reference for reference in declared}
    consistent = len(given) == len(explicit) and all(
        item.reference_id in by_id
        and (item.product_id, item.timeframe)
        == (by_id[item.reference_id].product_id, by_id[item.reference_id].timeframe)
        for item in explicit
    )
    if not consistent:
        for item in explicit:
            resolver.resolve(
                DatasetNeed(item.product_id, item.timeframe, "reference", item.reference_id),
                item.dataset_fingerprint,
            )
        return explicit
    bound: list[ReferenceInstrumentDataset] = []
    for reference in declared:
        sent = given.get(reference.id)
        fingerprint = resolver.resolve(
            DatasetNeed(reference.product_id, reference.timeframe, "reference", reference.id),
            None if sent is None else sent.dataset_fingerprint,
        )
        if fingerprint is not None:
            bound.append(
                ReferenceInstrumentDataset.model_validate(
                    {
                        "reference_id": reference.id,
                        "product_id": reference.product_id,
                        "timeframe": reference.timeframe,
                        "dataset_fingerprint": fingerprint,
                    }
                )
            )
    return tuple(bound)


def _bind_additional_instruments(
    start: BacktestStartRequest,
    definition: StrategyDefinition,
    resolver: DatasetResolver,
) -> tuple[AdditionalInstrumentDataset, ...]:
    """Bind every extra covered product, in lockstep order, unless the caller listed them.

    A caller-supplied product binding keeps its fingerprints and gains only omitted
    HTF and extra-clock datasets. Products the caller listed that the strategy does
    not cover are kept as sent so validation rejects them.
    """
    primary = definition.instrument.product_id
    products = tuple(item for item in lockstep_product_ids(definition) if item != primary)
    explicit = {item.product_id: item for item in start.additional_instrument_datasets}
    if len(explicit) != len(start.additional_instrument_datasets) or not set(explicit) <= set(
        products
    ):
        return start.additional_instrument_datasets
    bound: list[AdditionalInstrumentDataset] = []
    for product_id in products:
        given = explicit.get(product_id)
        clocks = bind_product_clocks(
            definition,
            product_id,
            resolver,
            dataset_fingerprint=None if given is None else given.dataset_fingerprint,
            htf_dataset_fingerprint=None if given is None else given.htf_dataset_fingerprint,
            indicator_dataset_fingerprints=(
                () if given is None else given.indicator_dataset_fingerprints
            ),
        )
        if clocks.dataset_fingerprint is None:
            continue
        bound.append(
            AdditionalInstrumentDataset(
                product_id=product_id,
                dataset_fingerprint=clocks.dataset_fingerprint,
                htf_dataset_fingerprint=clocks.htf_dataset_fingerprint,
                indicator_dataset_fingerprints=clocks.indicator_dataset_fingerprints,
            )
        )
    return tuple(bound)
