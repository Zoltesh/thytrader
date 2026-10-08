/** Portfolio fixed copy, status labels, and number/date text. Re-exported by `portfolios.ts`. */
import { positionStateLabel } from './deployments';
import { formatQuoteAmount } from './deployment-portfolio';
import { compareDecimalStrings, formatPercent } from './portfolio';
import type {
	JournalEntry,
	JournalKind,
	Portfolio,
	PortfolioBacktestJob,
	PortfolioDeploymentState,
	PortfolioMode,
	Proposal,
	ProposalKind,
	SleeveDeployment,
	SleeveIssueCode
} from './portfolios-types';
import { roundDecimal, shiftDecimal } from './portfolios-decimal';

export const CONFLICT_RELOADED = 'Changed elsewhere; reloaded — try again.';

export const LIMITS_NOTE =
	'On a deployed portfolio these limits bind every sleeve: each new entry must fit the total and per-asset exposure caps, and the daily loss and drawdown stops pause every sleeve and stay latched until you reset them. Portfolio backtests do not simulate them.';

export const LIMITS_ORDER =
	"Every order from every sleeve passes the sleeve's own risk checks, then these portfolio limits, then the account-wide risk policy. The strictest limit wins.";

export const MANAGER_NOTE =
	'The manager agent runs outside ThyTrader (Hermes or Claude through the thytrader-portfolio skill). It reads the briefing and submits proposals with its reasons; inside these permissions a proposal applies on its own, and everything else waits for you below.';

export const START_NOTE =
	'Starting runs one bot per sleeve with weight × capital. Strategies place every trade; the portfolio limits and the account risk policy check each one.';

export const MANAGER_NEVER: readonly string[] = [
	'Place orders itself: strategies place every trade',
	'Raise limits or add sleeves directly (it can only propose a sleeve)',
	'Resume anything paused by you or a loss breaker'
];

/** `0.3333` → `33.33%`, `0.5` → `50%` (exact; weights carry at most four places). */
export function weightPercent(fraction: string | null): string {
	return fraction === null ? 'unknown' : `${shiftDecimal(fraction, 2)}%`;
}

/** `1,234.50 USDC` (display rounding to cents; data stays exact). */
export function quoteText(amount: string | null, currency: string): string {
	return amount === null ? 'unknown' : `${formatQuoteAmount(amount)} ${currency}`;
}

/** A signed quote amount (`+12.30 USDC`, `-4.80 USDC`, `0.00 USDC`). */
export function signedQuote(amount: string | null, currency: string): string {
	if (amount === null) return 'unknown';
	const text = quoteText(amount, currency);
	return compareDecimalStrings(amount, '0') > 0 ? `+${text}` : text;
}

/** A signed fraction as a percent with two decimals (`+14.92%`, `-4.80%`). */
export function signedPercent(fraction: string): string {
	const text = formatPercent(fraction);
	return compareDecimalStrings(fraction, '0') > 0 ? `+${text}` : text;
}

/** A drawdown fraction (stored positive) shown as a loss (`-4.80%`, or `0.00%`). */
export function drawdownPercent(fraction: string): string {
	return compareDecimalStrings(fraction, '0') > 0 ? `-${formatPercent(fraction)}` : '0.00%';
}

/** Portfolio return points contributed by one sleeve (`+9.20 pts`). */
export function contributionPoints(fraction: string): string {
	return `${signedPercent(fraction).replace('%', '')} pts`;
}

/** A correlation coefficient with two decimals, or an em dash when undefined. */
export function coefficientText(value: string | null): string {
	return value === null ? '—' : roundDecimal(value, 2);
}

/** Sharpe and other ratios with two decimals, or an em dash when undefined. */
export function ratioText(value: string | null): string {
	return value === null ? '—' : roundDecimal(value, 2);
}

export function modeLabel(mode: PortfolioMode): 'LIVE' | 'Paper' {
	return mode === 'live' ? 'LIVE' : 'Paper';
}

/**
 * Card subtitle: what the portfolio is, its quote, its sleeves, and its state. The live
 * deployment view's state wins over the (possibly older) state on the portfolio itself.
 */
export function portfolioSubtitle(
	portfolio: Portfolio,
	liveState: PortfolioDeploymentState | null = null
): string {
	const venue = portfolio.mode === 'live' ? 'Real Coinbase spot' : 'Simulated';
	const sleeves = portfolio.sleeves.length;
	const state = deploymentStateLabel(liveState ?? portfolio.deployment_state ?? 'not_deployed');
	return `${venue} · ${portfolio.quote_currency} · ${sleeves} sleeve${sleeves === 1 ? '' : 's'} · ${state}`;
}

const STATE_LABELS: Record<PortfolioDeploymentState, string> = {
	not_deployed: 'Not deployed',
	running: 'Running',
	partially_running: 'Partly running',
	paused: 'Paused',
	stopped: 'Stopped'
};

export function deploymentStateLabel(state: PortfolioDeploymentState | string): string {
	return state in STATE_LABELS ? STATE_LABELS[state as PortfolioDeploymentState] : state;
}

/** True while a portfolio breaker pause (`PORTFOLIO_*_STOP:`) holds a sleeve. */
export function pausedByBreaker(
	deployment: Pick<SleeveDeployment, 'status' | 'mismatch_detail'>
): boolean {
	return (
		deployment.status === 'paused' &&
		(deployment.mismatch_detail ?? '').startsWith('PORTFOLIO_') &&
		(deployment.mismatch_detail ?? '').includes('_STOP:')
	);
}

/** `Running`, `Paused`, `Paused by breaker`, `Stopping (flatten)`, `Stopped`. */
export function botStatusText(deployment: SleeveDeployment): string {
	if (pausedByBreaker(deployment)) return 'Paused by breaker';
	if (deployment.status === 'stopped') {
		return deployment.lifecycle_command === 'flatten' &&
			deployment.open_books !== null &&
			deployment.open_books > 0
			? 'Stopping (flatten)'
			: 'Stopped';
	}
	return `${deployment.status.charAt(0).toUpperCase()}${deployment.status.slice(1)}`;
}

/**
 * What the sleeve bot's books are doing (ADR 0097): "Open · protected" while a TP/SL
 * rests, "Exiting" only while an exit is sent. Null when flat or not reported.
 */
export function sleevePositionText(deployment: SleeveDeployment): string | null {
	if (deployment.open_books === 0 && deployment.position_state !== 'open_unverified') return null;
	return positionStateLabel(deployment.position_state);
}

const BREAKER_LABELS: Record<string, string> = {
	PORTFOLIO_DAILY_LOSS_STOP: 'Daily loss stop',
	PORTFOLIO_DRAWDOWN_STOP: 'Max drawdown stop'
};

export function breakerLabel(code: string | null): string {
	return code === null ? 'No breaker' : (BREAKER_LABELS[code] ?? code);
}

const PROPOSAL_KIND_LABELS: Record<ProposalKind, string> = {
	rebalance: 'Rebalance',
	pause_sleeve: 'Pause a sleeve',
	resume_sleeve: 'Resume a sleeve',
	add_sleeve: 'Add a sleeve'
};

export function proposalKindLabel(kind: ProposalKind | string): string {
	return kind in PROPOSAL_KIND_LABELS ? PROPOSAL_KIND_LABELS[kind as ProposalKind] : kind;
}

/** `Applied automatically`, `Approved`, `Declined`, `Could not apply`, `Expired`, `Waiting`. */
export function proposalStatusText(proposal: Proposal): string {
	switch (proposal.status) {
		case 'pending':
			return 'Waiting for you';
		case 'applied':
			return proposal.auto_applied ? 'Applied automatically' : 'Approved';
		case 'declined':
			return 'Declined';
		case 'failed':
			return `Could not apply${proposal.failure_message ? `: ${proposal.failure_message}` : ''}`;
		case 'expired':
			return 'Expired unanswered';
	}
}

/** `BTC 50% (limit 60%)`, flagged when the largest asset is above the per-asset limit. */
export function largestAssetText(portfolio: Portfolio): { text: string; over: boolean } {
	const largest = portfolio.allocation.largest_asset;
	if (largest === null) return { text: 'No sleeves yet', over: false };
	const limit = weightPercent(portfolio.limits.max_per_asset_fraction);
	return {
		text: `${largest.asset} ${weightPercent(largest.weight_fraction)} (limit ${limit})`,
		over: portfolio.allocation.largest_asset_within_limit === false
	};
}

export function baseAsset(productId: string): string {
	const separator = productId.indexOf('-');
	return separator === -1 ? productId : productId.slice(0, separator);
}

const ISSUE_TEXT: Record<SleeveIssueCode, string> = {
	strategy_invalid: 'Rules invalid: fix the strategy before backtesting',
	quote_currency_mismatch: 'Quote currency changed: this sleeve no longer fits',
	product_unknown: 'No readable market: fix and save the strategy'
};

export function sleeveIssueText(code: SleeveIssueCode | string): string {
	return code in ISSUE_TEXT ? ISSUE_TEXT[code as SleeveIssueCode] : code;
}

const JOURNAL_KIND_LABELS: Record<JournalKind, string> = {
	created: 'Created',
	settings_changed: 'Settings changed',
	sleeve_added: 'Sleeve added',
	sleeve_updated: 'Sleeve updated',
	sleeve_removed: 'Sleeve removed',
	weights_changed: 'Weights changed',
	limits_changed: 'Limits changed',
	manager_changed: 'Manager settings changed',
	backtest_run: 'Backtest run',
	deployment_started: 'Started',
	deployment_paused: 'Paused',
	deployment_resumed: 'Resumed',
	deployment_stopped: 'Stopped',
	breaker_tripped: 'Breaker tripped',
	breaker_reset: 'Breaker reset',
	proposal_submitted: 'Proposal',
	proposal_approved: 'Proposal approved',
	proposal_declined: 'Proposal declined',
	proposal_failed: 'Proposal failed'
};

export function journalKindLabel(kind: string): string {
	return kind in JOURNAL_KIND_LABELS ? JOURNAL_KIND_LABELS[kind as JournalKind] : kind;
}

/** Who acted: you in the browser, an operator through the API/CLI, the system, or the manager. */
export function journalActorText(entry: Pick<JournalEntry, 'actor' | 'channel'>): string {
	if (entry.actor === 'manager') return 'Manager agent';
	if (entry.actor === 'system') return 'System';
	return entry.channel === 'browser' ? 'You (browser)' : 'Operator (API or CLI)';
}

/** `2026-10-02 14:05 UTC`. */
export function utcMinute(iso: string): string {
	const parsed = Date.parse(iso);
	if (Number.isNaN(parsed)) return iso;
	return `${new Date(parsed).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/** Progress copy for a job: queued, running sleeve N of M, combining, or its outcome. */
export function jobProgressText(job: PortfolioBacktestJob): string {
	switch (job.status) {
		case 'queued':
			return 'Queued…';
		case 'running':
			return job.progress_current < job.sleeve_count
				? `Running sleeve ${job.progress_current + 1} of ${job.sleeve_count}…`
				: 'Combining sleeves…';
		case 'completed':
			return 'Completed';
		case 'failed':
			return `Failed: ${job.error_message ?? 'no details returned'}`;
		case 'expired':
			return 'Expired before it finished. Run it again.';
		case 'cancelled':
			return 'Cancelled';
	}
}

/** A bar clock in seconds as `15m`, `4h`, or `1d`. */
export function durationText(seconds: number): string {
	if (seconds > 0 && seconds % 86_400 === 0) return `${seconds / 86_400}d`;
	if (seconds > 0 && seconds % 3_600 === 0) return `${seconds / 3_600}h`;
	return `${Math.max(1, Math.round(seconds / 60))}m`;
}

/** `2026-01-01 → 2026-09-30 · 272 days`. */
export function windowText(start: string, end: string): string {
	const startMs = Date.parse(start);
	const endMs = Date.parse(end);
	if (Number.isNaN(startMs) || Number.isNaN(endMs)) return `${start} → ${end}`;
	const days = Math.round((endMs - startMs) / 86_400_000);
	return `${start.slice(0, 10)} → ${end.slice(0, 10)} · ${days} day${days === 1 ? '' : 's'}`;
}
