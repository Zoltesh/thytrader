"""Portable campaign reports from frozen intent and bounded child evidence."""

import csv
from io import StringIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.research.campaigns import CampaignRecord


def campaign_csv(record: CampaignRecord) -> str:
    """Export one row per frozen case without loading trades, candles, or equity arrays."""
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "campaign_id",
            "manifest_fingerprint",
            "case",
            "strategy_id",
            "strategy_fingerprint",
            "evaluation_start",
            "evaluation_end",
            "deadline",
            "status",
            "job_id",
            "result_fingerprint",
            "maker_fee_rate",
            "taker_fee_rate",
            "slippage_bps",
            "spread_bps",
            "execution_stress",
            "trade_count",
            "net_return_fraction",
            "maximum_drawdown_fraction",
            "minimum_trades",
        )
    )
    for case, state in zip(record.manifest.cases, record.cases, strict=True):
        request, result = case.request, state.result
        key = "'" + case.key if case.key.startswith(("=", "+", "-", "@")) else case.key
        writer.writerow(
            (
                str(record.manifest.campaign_id),
                record.manifest_fingerprint,
                key,
                str(request.strategy_id),
                case.strategy_fingerprint,
                request.evaluation_start,
                request.evaluation_end,
                record.manifest.deadline,
                state.status.value,
                state.job_id or "",
                "" if result is None else result.result_fingerprint,
                request.maker_fee_rate,
                request.taker_fee_rate,
                request.fixed_slippage_bps,
                request.spread_bps or "0",
                ""
                if request.execution_stress is None
                else request.execution_stress.model_dump_json(),
                "" if result is None else result.summary.trade_count,
                "" if result is None else result.summary.total_return_fraction,
                "" if result is None else result.summary.maximum_drawdown_fraction,
                record.manifest.gates.minimum_trades,
            )
        )
    return output.getvalue()
