/**
 * Human text for one backtest's evaluation window and entry / exit diagnostics
 * (ADR 0090, 0093, 0094). Re-exported by `backtests.ts`.
 */
import type {
	BacktestDiagnostics,
	BacktestEvaluationWindow,
	BacktestExitReason
} from './backtests-types';

/** `2026-03-01` for midnight bars, else `2026-03-01 14:00 UTC`. */
function barStamp(value: string): string {
	const iso = new Date(value).toISOString();
	return iso.slice(11, 16) === '00:00'
		? iso.slice(0, 10)
		: `${iso.slice(0, 16).replace('T', ' ')} UTC`;
}

/** `Evaluated 2021-03-02 → 2026-02-28 · 1825 × 1d bars · after a 60-bar warmup from 2021-01-01`. */
export function formatEvaluationWindow(window: BacktestEvaluationWindow): string {
	return (
		`Evaluated ${barStamp(window.first_evaluated_bar)} → ${barStamp(window.last_evaluated_bar)}` +
		` · ${window.evaluation_bars} × ${window.timeframe} bars` +
		` · after a ${window.warmup_bars}-bar warmup from ${barStamp(window.warmup_start)}`
	);
}

const SKIP_REASON_LABELS: Record<string, string> = {
	pending_entry: 'An entry was already resting',
	cooldown: 'Cooling down after an exit',
	max_positions: 'Max concurrent positions reached',
	in_position: 'Already in a position (no pyramiding add allowed)',
	entry_price_not_positive: 'Entry price not positive',
	stop_distance_not_positive: 'ATR stop distance was zero',
	stop_not_positive: 'Long stop would be at or below zero',
	target_not_positive: 'Short take-profit would be at or below zero',
	stop_within_price_increment: 'Stop rounds onto the entry price',
	target_within_price_increment: 'Take-profit rounds onto the entry price',
	sizing_cash_unavailable: 'Sizing cash unknown',
	no_open_position: 'No open position to add to',
	insufficient_cash: 'Not enough cash to fund the order',
	notional_below_minimum: 'Risk-sized order below min_quote_notional',
	quantity_below_venue_minimum: 'Quantity below the venue minimum',
	notional_below_venue_minimum: 'Notional below the venue minimum'
};

const EXIT_REASON_LABELS: Record<BacktestExitReason, string> = {
	stop_loss: 'stop loss',
	take_profit: 'take profit',
	time_exit: 'time exit',
	signal: 'signal exit',
	evaluation_end: 'evaluation end'
};

/** Human label for one trade exit reason (`signal` reads `signal exit`). */
export function formatExitReason(reason: string): string {
	return EXIT_REASON_LABELS[reason as BacktestExitReason] ?? reason.replace(/_/g, ' ');
}

/** `3 signal exit · 2 stop` in a stable order, or [] when exits were not counted. */
export function exitReasonLines(diagnostics: BacktestDiagnostics): string[] {
	return (diagnostics.exit_reasons ?? []).map(
		(item) => `${item.count} ${formatExitReason(item.reason)}`
	);
}

/** Human label for one skip reason code; unknown codes are shown verbatim. */
export function formatSkipReason(reason: string): string {
	return SKIP_REASON_LABELS[reason] ?? reason;
}

/** `12 matched → 9 rested → 7 filled`: the funnel headline of one result. */
export function formatDiagnosticsFunnel(diagnostics: BacktestDiagnostics): string {
	return `${diagnostics.signals_matched} signals matched → ${diagnostics.entries_rested} entries rested → ${diagnostics.entries_filled} filled`;
}

/** Non-zero reasons an entry rested but never became a trade, in a stable order. */
export function unfilledEntryLines(diagnostics: BacktestDiagnostics): string[] {
	const lines: [number, string][] = [
		[diagnostics.entries_expired, 'expired unfilled and canceled'],
		[diagnostics.entries_unfilled_at_end, 'still resting when the window ended'],
		[diagnostics.entries_refused_at_fill, 'refused at fill (cash no longer sufficient)'],
		[diagnostics.entries_repriced, 'reprices of unfilled entries'],
		[diagnostics.entries_size_capped, 'entries clamped by a max notional cap']
	];
	return lines.filter(([count]) => count > 0).map(([count, text]) => `${count} ${text}`);
}
