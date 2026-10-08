"""Dataset-binding checks of a backtest submission against the published strategy.

HTF, extra-timeframe, additional-instrument, and reference-instrument fingerprints
must match what the definition declares, in order, without reusing an identity.
These checks read only the request and definition, never a dataset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.backtest.submission_models import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
)
from thytrader.strategies.models import (
    lockstep_product_ids,
    reference_data_requirements,
    unbound_indicator_timeframes,
)

if TYPE_CHECKING:
    from thytrader.evaluation.models import AdditionalInstrumentDataset
    from thytrader.strategies.models import StrategyDefinition


def _reference_bindings_match(
    request: BacktestSubmissionRequest, definition: StrategyDefinition
) -> bool:
    """Whether the request binds exactly the strategy's references, in declaration order."""
    declared = tuple(
        (item.reference_id, item.product_id, item.timeframe)
        for item in request.reference_dataset_fingerprints
    )
    required = tuple(
        (item.reference_id, item.product_id, item.timeframe)
        for item in reference_data_requirements(definition)
    )
    return declared == required


def _require_reference_dataset_request(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Reject reference bindings that do not match the strategy's reference instruments."""
    if _reference_bindings_match(request, definition):
        return
    expected = ", ".join(
        f"{item.reference_id}={item.product_id} {item.timeframe}"
        for item in reference_data_requirements(definition)
    )
    raise BacktestSubmissionRejectedError(
        "reference_dataset_fingerprints must bind exactly the strategy's "
        "data_requirements.reference_instruments (reference_id, product_id, timeframe) in "
        f"declaration order: {expected or 'none declared'}."
    )


def _require_htf_request(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Reject HTF fingerprints that do not match the published definition."""
    has_filter = definition.htf_filter is not None
    has_fingerprint = request.htf_dataset_fingerprint is not None
    if has_filter and not has_fingerprint:
        raise BacktestSubmissionRejectedError(
            "Multi-timeframe strategies require htf_dataset_fingerprint."
        )
    if not has_filter and has_fingerprint:
        raise BacktestSubmissionRejectedError(
            "htf_dataset_fingerprint is only valid when the strategy declares htf_filter."
        )
    if has_fingerprint and request.htf_dataset_fingerprint == request.dataset_fingerprint:
        raise BacktestSubmissionRejectedError(
            "htf_dataset_fingerprint must differ from dataset_fingerprint."
        )


def _require_indicator_dataset_request(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Reject extra-TF fingerprints that do not match the published definition."""
    required = unbound_indicator_timeframes(definition)
    declared = tuple(item.timeframe for item in request.indicator_dataset_fingerprints)
    if declared != required:
        raise BacktestSubmissionRejectedError(
            "indicator_dataset_fingerprints must match the strategy extra indicator timeframes."
        )
    reserved = {request.dataset_fingerprint}
    if request.htf_dataset_fingerprint is not None:
        reserved.add(request.htf_dataset_fingerprint)
    fingerprints = [item.dataset_fingerprint for item in request.indicator_dataset_fingerprints]
    if any(fingerprint in reserved for fingerprint in fingerprints):
        raise BacktestSubmissionRejectedError(
            "indicator_dataset_fingerprints must differ from dataset_fingerprint and "
            "htf_dataset_fingerprint."
        )


def _extra_lockstep_product_ids(definition: StrategyDefinition) -> tuple[str, ...]:
    """Return extra covered product ids in lexicographic lockstep order."""
    return tuple(
        product_id
        for product_id in lockstep_product_ids(definition)
        if product_id != definition.instrument.product_id
    )


def _require_additional_product_order(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Require extra dataset bindings to list the extra covered products in lockstep order."""
    declared = tuple(item.product_id for item in request.additional_instrument_datasets)
    if declared != _extra_lockstep_product_ids(definition):
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets must match extra covered products in product_id order."
        )


def _require_additional_htf_fingerprint(
    binding: AdditionalInstrumentDataset, *, has_filter: bool
) -> None:
    """Require extra-product HTF fingerprints exactly when the document declares htf_filter."""
    if has_filter and binding.htf_dataset_fingerprint is None:
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets require htf_dataset_fingerprint when the "
            "strategy declares htf_filter."
        )
    if not has_filter and binding.htf_dataset_fingerprint is not None:
        raise BacktestSubmissionRejectedError(
            "additional-instrument HTF fingerprints are only valid with htf_filter."
        )


def _additional_request_fingerprints(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> list[str]:
    """Collect extra-product dataset identities and reject HTF or extra-TF mismatches."""
    required_clocks = unbound_indicator_timeframes(definition)
    has_filter = definition.htf_filter is not None
    extra_fingerprints: list[str] = []
    for binding in request.additional_instrument_datasets:
        extra_fingerprints.append(binding.dataset_fingerprint)
        _require_additional_htf_fingerprint(binding, has_filter=has_filter)
        if binding.htf_dataset_fingerprint is not None:
            extra_fingerprints.append(binding.htf_dataset_fingerprint)
        clocks = tuple(item.timeframe for item in binding.indicator_dataset_fingerprints)
        if clocks != required_clocks:
            raise BacktestSubmissionRejectedError(
                "additional-instrument extra-TF fingerprints must match the strategy clocks."
            )
        extra_fingerprints.extend(
            item.dataset_fingerprint for item in binding.indicator_dataset_fingerprints
        )
    return extra_fingerprints


def _require_additional_instrument_request(
    request: BacktestSubmissionRequest,
    definition: StrategyDefinition,
) -> None:
    """Reject extra product bindings that do not match the published document."""
    if not definition.additional_instruments and not request.additional_instrument_datasets:
        return
    _require_additional_product_order(request, definition)
    extra_fingerprints = _additional_request_fingerprints(request, definition)
    reserved = {request.dataset_fingerprint}
    if request.htf_dataset_fingerprint is not None:
        reserved.add(request.htf_dataset_fingerprint)
    reserved.update(item.dataset_fingerprint for item in request.indicator_dataset_fingerprints)
    if any(fingerprint in reserved for fingerprint in extra_fingerprints):
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets must differ from primary LTF/HTF/extra-TF identities."
        )
    if len(extra_fingerprints) != len(set(extra_fingerprints)):
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets identities must be unique."
        )
