"""Exact read-only margin classifications, independent of display rounding."""

from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import Literal


def margin_state(
    available: Decimal | None, threshold: Decimal | None, derisk_ratio: str | None
) -> Literal["critical", "warning", "healthy"] | None:
    """Compare exact products; absent/nonfinite/inexact capacity is unknown, never safe."""
    if (
        available is None
        or threshold is None
        or derisk_ratio is None
        or not available.is_finite()
        or not threshold.is_finite()
        or threshold <= 0
    ):
        return None
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            critical = threshold * Decimal(derisk_ratio)
            warning = critical * 2
            if not critical.is_finite() or critical <= 0 or not warning.is_finite():
                return None
            if available <= critical:
                return "critical"
            return "warning" if available < warning else "healthy"
    except DecimalException:
        return None
