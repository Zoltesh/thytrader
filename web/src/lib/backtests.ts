import {
	compareDecimalStrings,
	decimalChartGeometry,
	formatPercent as formatExactPercent,
	formatUsd,
	utcTimestampSeconds,
	type HonestLinePoint
} from './portfolio';
import type { BacktestEngine, ValidityLimitCode } from './backtest-model';

export { compareDecimalStrings };

export type BacktestSummary = {
	initial_equity: string;
	final_equity: string;
	total_net_pnl: string;
	total_return_fraction: string;
	gross_profit: string;
	gross_loss: string;
	win_rate: string;
	profit_factor: string | null;
	average_win: string | null;
	average_loss: string | null;
	trade_count: number;
	winning_trade_count: number;
	maximum_drawdown: string;
	maximum_drawdown_fraction: string;
	exposure_bars: number;
	evaluation_bars: number;
	/** Present only when the run was spread-stressed (`spread_bps` > 0). */
	total_spread_cost?: string | null;
	/** Modeling limits this result discloses. */
	validity_limits?: ValidityLimitCode[] | null;
};

export type CostAssumptions = {
	maker_fee_rate?: string | null;
	taker_fee_rate?: string | null;
	fixed_slippage_bps?: string | null;
	/** Constant total bid-ask spread stress; "0" when unstressed. */
	spread_bps?: string | null;
};

export type BacktestSummaryEntry = {
	result_fingerprint: string;
	run_fingerprint: string;
	/** Snapshot of the rules this run used. */
	strategy_fingerprint: string;
	strategy_id?: string | null;
	dataset_fingerprint: string;
	published_at: string;
	summary: BacktestSummary;
	window?: BacktestEvaluationWindow | null;
};

export type BacktestList = {
	entries: BacktestSummaryEntry[];
	limit: number;
	offset: number;
	returned: number;
	has_more?: boolean;
};

export type BacktestFill = {
	candle_starts_at: string;
	price: string;
	quantity: string;
	notional: string;
	fee: string;
	fee_rate?: string | null;
	/** Taker fills of spread-stressed runs only. */
	reference_price?: string | null;
	executable_side?: 'ask' | 'bid' | null;
	spread_cost?: string | null;
};

/** Why a simulated position closed; `signal` is the `exits.signal_exit` rule (ADR 0093). */
export type BacktestExitReason =
	'stop_loss' | 'take_profit' | 'time_exit' | 'signal' | 'evaluation_end';

export type BacktestTrade = {
	entry: BacktestFill;
	exit: BacktestFill & {
		reason: BacktestExitReason;
	};
	gross_pnl: string;
	net_pnl: string;
	holding_bars: number;
};

export type EquityPoint = {
	candle_starts_at: string;
	cash: string;
	base_quantity: string;
	mark_price: string;
	equity: string;
};

export type BacktestResult = {
	schema_version: '1.0';
	engine: BacktestEngine;
	run_fingerprint: string;
	strategy_fingerprint: string;
	dataset_fingerprint: string;
	signal_trace_fingerprint: string;
	trades: BacktestTrade[];
	equity_curve: EquityPoint[];
	summary: BacktestSummary;
};

/** One reason a matched signal rested no entry, with how many signals it stopped. */
export type BacktestSkipCount = { reason: string; count: number };

/** Closed trades per exit reason (ADR 0093); sums to the result's trade count. */
export type BacktestExitCount = { reason: BacktestExitReason; count: number };

/**
 * Entry-funnel counters stored beside (never inside) a result (ADR 0090). Null on
 * results published before they were recorded.
 */
export type BacktestDiagnostics = {
	diagnostics_version: 'thytrader-backtest-diagnostics-v1';
	signals_matched: number;
	entries_rested: number;
	entries_filled: number;
	entries_expired: number;
	entries_repriced: number;
	entries_refused_at_fill: number;
	entries_unfilled_at_end: number;
	entries_size_capped: number;
	warmup_bars: number;
	skipped: BacktestSkipCount[];
	/** Null on diagnostics recorded before exits were counted (ADR 0093). */
	exit_reasons?: BacktestExitCount[] | null;
};

/**
 * The bars one result evaluated, derived from its run outside the result bytes (ADR 0094).
 * Omitted bounds start after each strategy's own warmup, so two strategies on one dataset
 * can cover different bars: compare them only on matching evaluation_start/evaluation_end.
 */
export type BacktestEvaluationWindow = {
	timeframe: string;
	evaluation_start: string;
	/** Exclusive bound: its bar's open liquidates any position still held. */
	evaluation_end: string;
	first_evaluated_bar: string;
	last_evaluated_bar: string;
	evaluation_bars: number;
	warmup_bars: number;
	warmup_start: string;
};

export type BacktestDetail = {
	result: BacktestResult;
	result_fingerprint: string;
	costs?: CostAssumptions | null;
	diagnostics?: BacktestDiagnostics | null;
	window?: BacktestEvaluationWindow | null;
};

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

export type BacktestBenchmark = {
	benchmark_contract_version: 'thytrader-buy-and-hold-v1';
	benchmark_fingerprint: string;
	result_fingerprint: string;
	run_fingerprint: string;
	dataset_fingerprint: string;
	engine: BacktestEngine;
	entry_candle_starts_at: string;
	exit_candle_starts_at: string;
	entry_price: string;
	exit_price: string;
	initial_equity: string;
	final_equity: string;
	total_net_pnl: string;
	total_return_fraction: string;
	total_fees: string;
	total_spread_cost?: string | null;
	maximum_drawdown: string;
	maximum_drawdown_fraction: string;
	evaluation_bars: number;
};

export type BacktestBenchmarkResponse = {
	benchmark: BacktestBenchmark;
	result_fingerprint: string;
};

export type BacktestPerformanceMetrics = {
	metrics_contract_version: 'thytrader-performance-metrics-v1';
	metrics_fingerprint: string;
	result_fingerprint: string;
	run_fingerprint: string;
	engine: BacktestEngine;
	risk_free_rate: string;
	annualization: 'equity_curve_bar_clock';
	bar_seconds?: string | null;
	bars_per_year?: string | null;
	sharpe?: string | null;
	sortino?: string | null;
	calmar?: string | null;
	sqn?: string | null;
	cagr?: string | null;
	annualized_volatility?: string | null;
	max_consecutive_losses: number;
	exposure_fraction: string;
	buy_and_hold_return_fraction?: string | null;
};

export type BacktestMetricsResponse = {
	metrics: BacktestPerformanceMetrics;
	result_fingerprint: string;
};

export type ApiError = {
	detail?: { code?: string; message?: string };
};

export function formatPercent(fraction: string): string {
	return formatExactPercent(fraction);
}

export type BacktestEquitySample = {
	time: number;
	amount: string;
	date: string;
	value: number;
};

export type BacktestEquityChartModel = {
	series: HonestLinePoint[];
	samples: BacktestEquitySample[];
	minAmount: string;
	maxAmount: string;
};

const EMPTY_BACKTEST_EQUITY_MODEL: BacktestEquityChartModel = {
	series: [],
	samples: [],
	minAmount: '0',
	maxAmount: '0'
};

export function backtestEquityChartModel(points: readonly EquityPoint[]): BacktestEquityChartModel {
	/** Build a time-ordered mark-to-model series; Y uses finite chart geometry only. */
	const dated: Array<{ date: string; amount: string; time: number }> = [];
	const usedTimes = new Set<number>();
	for (const point of points) {
		const time = utcTimestampSeconds(point.candle_starts_at);
		if (time === null || usedTimes.has(time)) continue;
		usedTimes.add(time);
		dated.push({ date: point.candle_starts_at, amount: point.equity, time });
	}
	if (dated.length < 2) {
		return EMPTY_BACKTEST_EQUITY_MODEL;
	}
	dated.sort((left, right) => left.time - right.time);

	const { values, minAmount, maxAmount } = decimalChartGeometry(dated.map((entry) => entry.amount));
	const samples: BacktestEquitySample[] = dated.map((entry, index) => ({
		time: entry.time,
		amount: entry.amount,
		date: entry.date,
		value: values[index] ?? 0
	}));
	return {
		series: samples.map((sample) => ({ time: sample.time, value: sample.value })),
		samples,
		minAmount,
		maxAmount
	};
}

export function shortFingerprint(fingerprint: string): string {
	return `${fingerprint.slice(0, 16)}…${fingerprint.slice(-8)}`;
}

function isRecordedDecimal(value: string | null | undefined): value is string {
	return typeof value === 'string' && value.length > 0;
}

function formatDisplayFeeRate(rate: string): string {
	try {
		return formatPercent(rate);
	} catch {
		return rate;
	}
}

/** Stop-first on the fill candle; later candles match a resting take-profit first. */
export const SAME_BAR_POLICY_LABEL = 'Fill candle: stop first';

/** The recorded spread stress in bps when it is greater than zero; otherwise null. */
export function spreadStressBps(costs?: CostAssumptions | null): string | null {
	const bps = costs?.spread_bps;
	if (!isRecordedDecimal(bps)) return null;
	try {
		return compareDecimalStrings(bps, '0') > 0 ? bps : null;
	} catch {
		return null;
	}
}

export function formatPublishedCosts(costs?: CostAssumptions | null): string {
	if (!costs) {
		return 'Recorded maker/taker fee rates and fixed_slippage_bps are not included in this response.';
	}
	const maker = isRecordedDecimal(costs.maker_fee_rate)
		? `maker ${formatDisplayFeeRate(costs.maker_fee_rate)}`
		: 'maker fee not recorded';
	const taker = isRecordedDecimal(costs.taker_fee_rate)
		? `taker ${formatDisplayFeeRate(costs.taker_fee_rate)}`
		: 'taker fee not recorded';
	const slippage = isRecordedDecimal(costs.fixed_slippage_bps)
		? `fixed slippage ${costs.fixed_slippage_bps} bps on taker exits`
		: 'fixed_slippage_bps not recorded';
	return `${maker} · ${taker} · ${slippage} (modeled research-run cost assumptions, not observed Coinbase fees)`;
}

/**
 * Spread-stress disclosure for one result, or null when the run was not stressed
 * and recorded no spread cost.
 */
export function formatSpreadCostNote(
	costs: CostAssumptions | null | undefined,
	totalSpreadCost: string | null | undefined
): string | null {
	const bps = spreadStressBps(costs);
	const cost = isRecordedDecimal(totalSpreadCost) ? totalSpreadCost : null;
	if (bps === null && cost === null) return null;
	const parts: string[] = [];
	if (bps !== null) parts.push(`Spread stress ${bps} bps (total bid-ask)`);
	if (cost !== null) parts.push(`total modeled spread cost ${formatUsd(cost)}`);
	return `${parts.join(' · ')}. This is a disclosed stress input, not observed bid/ask data.`;
}

export function formatFillFee(fill: Pick<BacktestFill, 'fee' | 'fee_rate'>): string {
	const amount = formatUsd(fill.fee);
	if (!isRecordedDecimal(fill.fee_rate)) {
		return `${amount} (fee rate not recorded)`;
	}
	return `${amount} (${formatDisplayFeeRate(fill.fee_rate)})`;
}

/** Default UI page size; the API accepts limits from 1 through 100. */
export const BACKTEST_LIST_DEFAULT_LIMIT = 10;

export const RESULT_FINGERPRINT_PATTERN = /^sha256:[0-9a-f]{64}$/;

export type BacktestListQuery = {
	limit?: number;
	offset?: number;
	strategy_fingerprint?: string;
	strategy_id?: string;
};

export function formatBacktestListBound(
	listing: Pick<BacktestList, 'limit' | 'offset' | 'returned'>
): string {
	/** Disclose the newest-first page bound without implying a complete archive. */
	const { returned, offset } = listing;
	if (offset === 0) {
		return `Showing ${returned} (newest)`;
	}
	return `Showing ${returned} (newest-first, offset ${offset})`;
}

export function backtestListPageIsFull(listing: Pick<BacktestList, 'limit' | 'returned'>): boolean {
	return listing.returned === listing.limit;
}

export function formatListSpreadCue(totalSpreadCost: string | null | undefined): string | null {
	/** Surface recorded modeled spread on a list row; omit when the API did not record one. */
	if (!isRecordedDecimal(totalSpreadCost)) {
		return null;
	}
	return `spread ${formatUsd(totalSpreadCost)} recorded`;
}

export function parseResultFingerprintParam(value: string | null): string | null {
	/** Accept only canonical sha256 result identities from `?result=`. */
	if (value === null || RESULT_FINGERPRINT_PATTERN.exec(value) === null) {
		return null;
	}
	return value;
}

export async function fetchBacktests(
	query: BacktestListQuery = {},
	signal?: AbortSignal
): Promise<BacktestList> {
	const params = new URLSearchParams();
	if (query.limit !== undefined) {
		params.set('limit', String(query.limit));
	}
	if (query.offset !== undefined) {
		params.set('offset', String(query.offset));
	}
	if (query.strategy_fingerprint !== undefined) {
		params.set('strategy_fingerprint', query.strategy_fingerprint);
	}
	if (query.strategy_id !== undefined) {
		params.set('strategy_id', query.strategy_id);
	}
	const search = params.toString();
	const response = await fetch(
		search === '' ? '/api/v1/backtests' : `/api/v1/backtests?${search}`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest results are unavailable.');
	}
	return (await response.json()) as BacktestList;
}

export async function fetchBacktest(
	resultFingerprint: string,
	signal?: AbortSignal
): Promise<BacktestDetail> {
	// The route defaults to `detail=summary` (no `result`, trades, or equity curve);
	// the detail view needs the full document.
	const response = await fetch(
		`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}?detail=full`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest result is unavailable.');
	}
	return (await response.json()) as BacktestDetail;
}

export async function fetchBacktestBenchmark(
	resultFingerprint: string,
	signal?: AbortSignal
): Promise<BacktestBenchmarkResponse> {
	const response = await fetch(
		`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}/benchmark`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest benchmark is unavailable.');
	}
	return (await response.json()) as BacktestBenchmarkResponse;
}

export async function fetchBacktestMetrics(
	resultFingerprint: string,
	signal?: AbortSignal
): Promise<BacktestMetricsResponse> {
	const response = await fetch(
		`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}/metrics`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest metrics are unavailable.');
	}
	return (await response.json()) as BacktestMetricsResponse;
}

/**
 * Every saved result for one exact strategy snapshot fingerprint, newest first.
 *
 * Follows offset pages (bounded) so a snapshot's evidence list never silently
 * stops at the first page.
 */
export async function fetchAllBacktestsForFingerprint(
	strategyFingerprint: string,
	pageSize = 50,
	maxPages = 20
): Promise<BacktestSummaryEntry[]> {
	const rows: BacktestSummaryEntry[] = [];
	let offset = 0;
	for (let page = 0; page < maxPages; page += 1) {
		const listing = await fetchBacktests({
			limit: pageSize,
			offset,
			strategy_fingerprint: strategyFingerprint
		});
		rows.push(...listing.entries);
		if (listing.returned < pageSize || listing.has_more === false) return rows;
		offset += listing.returned;
	}
	return rows;
}

/**
 * Every saved result for one strategy (`?strategy_id=`), newest first,
 * across all of its snapshots. Follows bounded offset pages.
 */
export async function fetchAllBacktestsForStrategy(
	strategyId: string,
	pageSize = 50,
	maxPages = 20
): Promise<BacktestSummaryEntry[]> {
	const rows: BacktestSummaryEntry[] = [];
	let offset = 0;
	for (let page = 0; page < maxPages; page += 1) {
		const listing = await fetchBacktests({ limit: pageSize, offset, strategy_id: strategyId });
		rows.push(...listing.entries);
		if (listing.returned < pageSize || listing.has_more === false) return rows;
		offset += listing.returned;
	}
	return rows;
}

/**
 * Request field for the optional spread-stress input: omitted when blank or zero
 * (the server default is no stress); any other text is sent for server validation.
 */
export function optionalSpreadStress(raw: string): { spread_bps?: string } {
	const value = raw.trim();
	if (value === '') return {};
	try {
		if (compareDecimalStrings(value, '0') === 0) return {};
	} catch {
		// Not a decimal: send it so the server returns its validation message.
	}
	return { spread_bps: value };
}
