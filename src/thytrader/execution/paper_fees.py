"""Paper fee rates for new books: the operator's explicit rates or the account's own.

A paper book that omits fee rates models what the Coinbase account actually pays. When
those rates cannot be read the start is refused rather than given invented defaults; the
operator can still pass both rates explicitly. Existing books keep their stored rates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.models import ExecutionConflictError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from decimal import Decimal

# Returns the account's current (maker, taker) rates or raises PaperFeesUnavailableError.
type PaperFeeSource = Callable[[], Awaitable[tuple[Decimal, Decimal]]]


class PaperFeesUnavailableError(ExecutionConflictError):
    """The account's fee rates are unknown, so a paper start without explicit rates is refused."""


def paper_fees_unavailable(reason: str) -> PaperFeesUnavailableError:
    """Explain the refusal and the explicit-rate alternative without inventing rates."""
    return PaperFeesUnavailableError(
        "Paper books use your Coinbase account's fee rates, which are unavailable "
        f"({reason}). Pass both maker_fee_rate and taker_fee_rate "
        "(CLI: --maker-fee-rate and --taker-fee-rate) to start with explicit rates."
    )


async def paper_fee_rates(
    *,
    maker_fee_rate: Decimal | None,
    taker_fee_rate: Decimal | None,
    source: PaperFeeSource | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Fill omitted paper rates from the account source; explicit or partial rates pass through.

    Without a source (internal callers and tests) omitted rates stay omitted and the
    ledger's documented assumptions apply, as before.
    """
    if maker_fee_rate is not None or taker_fee_rate is not None or source is None:
        return maker_fee_rate, taker_fee_rate
    return await source()
