"""Parsers for ``thytrader-runtime`` installation configuration commands.

The risk-policy registry, YAML non-secret settings, and Coinbase credentials.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.runtime_control.parsers.common import _CONFIRM_HELP

if TYPE_CHECKING:
    import argparse

_ALLOCATION_HELP = (
    "Optional strategy_id:allocated_quote reservation. Repeatable. strategy_id is the "
    "strategy UUID (thytrader-research list-strategies, or strategy_id on "
    "thytrader-runtime show). Any allocation turns the policy into an allowlist: "
    "discretionary place-order and strategies without an allocation are denied."
)


def add_risk_policy_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register ``show-risk-policy`` and ``set-risk-policy``."""
    subparsers.add_parser(
        "show-risk-policy",
        parents=[trailing],
        help="Show the effective compiled or published risk policy.",
    )
    set_policy = subparsers.add_parser(
        "set-risk-policy",
        parents=[trailing],
        help="Publish a new immutable risk-policy version.",
    )
    set_policy.add_argument(
        "--quote-currency",
        choices=("USD", "USDC", "USDT"),
        default="USDC",
        help="Installation quote currency for the published policy. Default USDC.",
    )
    set_policy.add_argument(
        "--product-allowlist",
        action="append",
        default=[],
        help=(
            "Optional spot product such as BTC-USDC (BASE-QUOTE, matching --quote-currency). "
            "Repeatable. Empty means no extra restriction."
        ),
    )
    set_policy.add_argument("--max-concurrent-running-deployments", type=int, required=True)
    set_policy.add_argument("--max-concurrent-open-positions", type=int, required=True)
    set_policy.add_argument("--max-portfolio-exposure-fraction", required=True)
    set_policy.add_argument("--per-product-max-exposure-fraction", required=True)
    set_policy.add_argument("--paper-capital-quote", required=True)
    set_policy.add_argument(
        "--daily-loss-limit-fraction",
        default="1",
        help="UTC-day realized plus unrealized loss cap as a fraction of the mode capital base.",
    )
    set_policy.add_argument(
        "--max-strategy-drawdown-fraction",
        default="1",
        help="Per-strategy fill-ledger drawdown cap as a fraction of peak marked equity.",
    )
    set_policy.add_argument(
        "--max-entry-orders-per-minute",
        type=int,
        default=60,
        help="Rolling 60-second cap on new orders that blocks risk-increasing entries.",
    )
    set_policy.add_argument(
        "--max-cancellations-per-minute",
        type=int,
        default=60,
        help="Rolling 60-second cancel cap that blocks further risk-increasing entries.",
    )
    set_policy.add_argument(
        "--reference-price-collar-fraction",
        default="0.5",
        help="Maximum |limit-last_close|/last_close for risk-increasing priced entries.",
    )
    set_policy.add_argument(
        "--allow-intra-strategy-pyramiding",
        action="store_true",
        help="Permit same-side adds when the published strategy also enables pyramiding.",
    )
    set_policy.add_argument(
        "--max-daily-loss-quote",
        default=None,
        help=(
            "Optional absolute quote ceiling enforced alongside --daily-loss-limit-fraction "
            "(whichever bound is tighter trips first). Unset by default; this CLI does not "
            "assert a universal safe amount."
        ),
    )
    set_policy.add_argument(
        "--max-portfolio-exposure-quote",
        default=None,
        help=(
            "Optional absolute quote ceiling enforced alongside "
            "--max-portfolio-exposure-fraction. Unset by default."
        ),
    )
    set_policy.add_argument(
        "--max-venue-order-actions-per-minute",
        type=int,
        default=None,
        help=(
            "Optional combined per-minute cap across entry-order and cancellation requests "
            "(a coarse venue-request budget). Denies only new entries; unset by default."
        ),
    )
    set_policy.add_argument(
        "--max-order-quantity",
        default=None,
        help=(
            "Optional maximum base quantity for one new entry. Publication replaces the "
            "whole active policy: omitting this flag unsets the bound in the new version. "
            "Resupply it to retain an existing bound; historical versions remain immutable."
        ),
    )
    set_policy.add_argument(
        "--max-order-notional-quote",
        default=None,
        help=(
            "Optional maximum quote notional for one new entry. Omitting this flag unsets "
            "the bound in the replacement policy; resupply it to retain an existing bound."
        ),
    )
    set_policy.add_argument(
        "--min-available-quote-reserve",
        default=None,
        help=(
            "Optional same-quote notional admission headroom. Live fees/slippage are not "
            "included: this is not a guaranteed post-fill balance. Confirmed venue holds "
            "are not subtracted twice; ambiguous holds deny. Paper includes recorded cash "
            "debits and modeled fees. Requires matching --quote-currency. Omitting this "
            "flag unsets the reserve in the replacement policy; resupply it to retain it."
        ),
    )
    set_policy.add_argument(
        "--max-fleet-entries-per-window",
        type=int,
        default=None,
        help=(
            "Optional fleet entry clustering cap (ADR 0125): deny a new entry once this many "
            "distinct bot/product entries were placed in this mode within "
            "--fleet-entry-window-minutes, stopped bots included "
            "(FLEET_ENTRY_CLUSTER_LIMIT; does not pause). 1-128; requires "
            "--fleet-entry-window-minutes. Reprices, adoptions and protective exits are "
            "never gated. Omitting this flag unsets the cap in the replacement policy."
        ),
    )
    set_policy.add_argument(
        "--fleet-entry-window-minutes",
        type=int,
        default=None,
        help=(
            "Trailing window for --max-fleet-entries-per-window, 1-1440 minutes; set both "
            "or neither. Omitting this flag unsets the window in the replacement policy."
        ),
    )
    set_policy.add_argument(
        "--max-btc-beta-exposure-fraction",
        default=None,
        help=(
            "Optional BTC-beta-weighted exposure cap (ADR 0125), a fraction in (0, 1] of the "
            "capital base: sum of |exposure| x beta vs BTC-<quote> (90 settled daily bars) "
            "over same-quote bots plus the new entry (BTC_BETA_EXPOSURE_EXCEEDED). A new or "
            "held product without a fresh beta (fewer than 60 daily returns, unreadable, or "
            "over 48h old) blocks new entries (BTC_BETA_UNAVAILABLE). Applies to paper and "
            "live, pyramid adds, reprices and in-kind adoption; never to exits; does not "
            "pause. Omitting this flag unsets the cap in the replacement policy."
        ),
    )
    set_policy.add_argument(
        "--max-btc-beta-exposure-quote",
        default=None,
        help=(
            "Optional live-only absolute ceiling on BTC-beta-weighted exposure in the policy "
            "quote, enforced with --max-btc-beta-exposure-fraction (the tighter wins). "
            "Omitting this flag unsets it in the replacement policy."
        ),
    )
    set_policy.add_argument(
        "--futures-live-spot-collateral-reserve-quote",
        default=None,
        help=(
            "Shared USDC collateral (ADR 0129): Coinbase counts USDC as CFM futures "
            "collateral. While manual futures hold margin, new live USD/USDC spot entries are "
            "denied (FUTURES_COLLATERAL_IN_USE) unless this reserve, in the policy quote, is "
            "set: it must cover the CFM initial margin x --futures-peg-haircut (else "
            "FUTURES_COLLATERAL_RESERVE_SHORT) and is withheld from spot capital. A stale or "
            "failed futures read denies (FUTURES_COLLATERAL_UNKNOWN). Never gates exits. "
            "Omitting this flag unsets it."
        ),
    )
    set_policy.add_argument(
        "--futures-peg-haircut",
        default=None,
        help=(
            "Multiplier (>= 1.0, default 1.25) on the USD initial margin that the USDC "
            "reserve must cover; a threshold under a declared peg, never a sum."
        ),
    )
    set_policy.add_argument(
        "--allocation",
        action="append",
        default=[],
        help=_ALLOCATION_HELP,
    )
    set_policy.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)


def add_settings_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register ``show-settings`` and ``set-settings`` (YAML non-secrets and YOLO)."""
    subparsers.add_parser(
        "show-settings",
        parents=[trailing],
        help="Show YAML non-secret settings and YOLO without echoing secrets.",
    )
    set_settings = subparsers.add_parser(
        "set-settings",
        parents=[trailing],
        help="Write YAML non-secret settings. YOLO applies without restart.",
    )
    set_settings.add_argument(
        "--yolo-enabled",
        choices=("true", "false"),
        default=None,
        help="YOLO on/off. Independent of leftover THYTRADER_YOLO_ENABLED env.",
    )
    set_settings.add_argument(
        "--yolo-tiers",
        default=None,
        help=(
            "Independent YOLO tiers: data, research, paper, and/or live. "
            "Scalar paper is valid. Live still needs --i-understand-live. "
            "Empty string clears tiers (requires --yolo-enabled false)."
        ),
    )
    set_settings.add_argument(
        "--log-level",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        default=None,
    )
    set_settings.add_argument("--snapshot-interval-seconds", type=int, default=None)
    set_settings.add_argument("--market-data-worker-interval-seconds", type=int, default=None)
    set_settings.add_argument("--market-data-worker-lookback-hours", type=int, default=None)
    set_settings.add_argument("--market-data-worker-product-id", default=None)
    set_settings.add_argument("--execution-worker-interval-seconds", type=int, default=None)
    set_settings.add_argument(
        "--notify-provider",
        choices=("none", "log", "webhook"),
        default=None,
        help="Webhook URL stays in ignored .env and still needs a restart.",
    )
    set_settings.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)


def add_credential_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register write-only Coinbase credential show/set/clear."""
    subparsers.add_parser(
        "show-coinbase-credentials",
        parents=[trailing],
        help="Show whether Coinbase secrets are configured; never prints them.",
    )
    set_creds = subparsers.add_parser(
        "set-coinbase-credentials",
        parents=[trailing],
        help="Set or rotate Coinbase Advanced Trade secrets from a private-key file.",
    )
    set_creds.add_argument("--api-key-name", required=True, help="Coinbase API key name.")
    set_creds.add_argument(
        "--private-key-file",
        required=True,
        help="Path to the PEM private key. Never pass the key on the command line.",
    )
    set_creds.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    clear_creds = subparsers.add_parser(
        "clear-coinbase-credentials",
        parents=[trailing],
        help="Clear Coinbase Advanced Trade secrets and return this API process to demo.",
    )
    clear_creds.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
