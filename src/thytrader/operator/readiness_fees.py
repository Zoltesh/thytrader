"""Fee-evidence and paper-capital sections of the readiness report."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailure
from thytrader.execution.ledger import effective_paper_fee_rates
from thytrader.execution.lifecycle import occupies_running_slot
from thytrader.execution.models import Deployment, DeploymentMode, DeploymentSnapshot
from thytrader.operator.readiness_account import _ZERO, _product_quote
from thytrader.operator.readiness_models import (
    ReadinessFeeEvidence,
    ReadinessFeeGapRow,
    ReadinessFinding,
    ReadinessPaperSection,
    ReadinessSeverity,
)
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.portfolio.service import PortfolioService
    from thytrader.risk.models import ActiveRiskPolicy


async def _account_fee_profile(
    portfolio: PortfolioService,
) -> tuple[FeeProfile | None, ExchangeReadFailure | None]:
    """Read account fee evidence, or return a failure without inventing rates."""
    try:
        return await portfolio.get_fee_profile(), None
    except ExchangeReadError as error:
        return None, error.failure
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        return None, None


def _paper_fee_gap(
    deployment: Deployment, profile: FeeProfile | None, *, demo: bool
) -> ReadinessFeeGapRow | None:
    """Compare one paper book's assumptions with account evidence, if any."""
    if deployment.mode is not DeploymentMode.PAPER:
        return None
    maker, taker = effective_paper_fee_rates(
        deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
    )
    maker_gap = taker_gap = None
    optimistic = False
    if profile is not None and not demo:
        maker_gap = profile.maker_fee_rate - maker
        taker_gap = profile.taker_fee_rate - taker
        optimistic = maker_gap > 0 or taker_gap > 0
    return ReadinessFeeGapRow(
        deployment_id=deployment.id,
        product_id=deployment.product_id,
        assumed_maker_fee_rate=canonical_decimal(maker),
        assumed_taker_fee_rate=canonical_decimal(taker),
        account_maker_fee_rate=(
            None if profile is None else canonical_decimal(profile.maker_fee_rate)
        ),
        account_taker_fee_rate=(
            None if profile is None else canonical_decimal(profile.taker_fee_rate)
        ),
        maker_gap=None if maker_gap is None else canonical_decimal(maker_gap),
        taker_gap=None if taker_gap is None else canonical_decimal(taker_gap),
        more_optimistic=optimistic,
    )


def _record_fee_findings(
    findings: list[ReadinessFinding],
    *,
    compared: int,
    profile: FeeProfile | None,
    optimistic: Sequence[ReadinessFeeGapRow],
) -> None:
    """Disclose missing fee evidence or paper assumptions cheaper than the account."""
    if profile is None and compared:
        findings.append(
            ReadinessFinding(
                reason_code="FEE_EVIDENCE_UNAVAILABLE",
                severity=ReadinessSeverity.UNKNOWN,
                detail=(
                    "Account fee evidence is unavailable, so paper fee assumptions were "
                    "listed but not judged; no comparison was invented."
                ),
            )
        )
    if not optimistic:
        return
    worst = max(optimistic, key=lambda item: Decimal(item.maker_gap or "0"))
    findings.append(
        ReadinessFinding(
            reason_code="PAPER_FEE_ASSUMPTION_MORE_OPTIMISTIC",
            severity=ReadinessSeverity.ADVISORY,
            deployment_id=worst.deployment_id,
            detail=(
                f"{len(optimistic)} paper book(s) assume cheaper fees than the account "
                f"reports (e.g. maker {worst.assumed_maker_fee_rate} vs "
                f"{worst.account_maker_fee_rate}, taker {worst.assumed_taker_fee_rate} vs "
                f"{worst.account_taker_fee_rate}). Paper and backtest results read more "
                "optimistic than live fills; this disclosure changes no policy."
            ),
        )
    )


async def _fee_evidence(
    portfolio: PortfolioService,
    snapshots: Mapping[UUID, DeploymentSnapshot],
    findings: list[ReadinessFinding],
) -> ReadinessFeeEvidence:
    """Compare running and paused paper fee assumptions with the account's reported rates.

    A stopped book never fills again, so its stored rates cannot distort paper results.
    """
    profile, failure = await _account_fee_profile(portfolio)
    active = tuple(
        snapshot for snapshot in snapshots.values() if occupies_running_slot(snapshot.deployment)
    )
    gaps = tuple(
        row
        for snapshot in active
        if (row := _paper_fee_gap(snapshot.deployment, profile, demo=portfolio.demo)) is not None
    )
    optimistic = tuple(row for row in gaps if row.more_optimistic)
    defaulting = sum(
        1
        for snapshot in active
        if snapshot.deployment.mode is DeploymentMode.PAPER
        and (
            snapshot.deployment.paper_maker_fee_rate is None
            or snapshot.deployment.paper_taker_fee_rate is None
        )
    )
    _record_fee_findings(findings, compared=len(gaps), profile=profile, optimistic=optimistic)
    return ReadinessFeeEvidence(
        account_maker_fee_rate=(
            None if profile is None else canonical_decimal(profile.maker_fee_rate)
        ),
        account_taker_fee_rate=(
            None if profile is None else canonical_decimal(profile.taker_fee_rate)
        ),
        account_fee_tier=None if profile is None else profile.fee_tier,
        account_as_of=None if profile is None else profile.as_of,
        demo=portfolio.demo,
        unavailable_reason=None if profile is not None else "read_failure",
        read_failure=failure,
        paper_books_compared=len(gaps),
        paper_books_defaulting_rates=defaulting,
        optimistic_books=optimistic,
    )


def _paper_section(
    policy: ActiveRiskPolicy | None,
    deployments: Sequence[Deployment],
    warnings: list[str],
) -> ReadinessPaperSection | None:
    """Paper-book committed starting cash versus the policy's paper capital."""
    if policy is None:
        return None
    paper = [item for item in deployments if item.mode is DeploymentMode.PAPER]
    matching = [
        item
        for item in paper
        if _product_quote(item.product_id) == policy.definition.quote_currency
    ]
    if len(matching) != len(paper):
        warnings.append(
            "Paper starting-cash totals exclude other/unknown quote books; no FX assumed."
        )
    committed = sum((item.paper_starting_cash or _ZERO for item in matching), _ZERO)
    return ReadinessPaperSection(
        quote_currency=policy.definition.quote_currency,
        paper_capital_quote=policy.definition.paper_capital_quote,
        committed_starting_cash=canonical_decimal(committed),
        books=len(matching),
    )
