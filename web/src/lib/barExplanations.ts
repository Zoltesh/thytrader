/**
 * Bounded per-bar backtest explanations (`thytrader-backtest-bar-explanation-v1`).
 *
 * Each page reuses the verified signal trace and the immutable result. Fills on the
 * evaluation-end liquidation bar are listed as outside the trace, not as a signal bar.
 */

export const BAR_EXPLANATION_SCHEMA = 'thytrader-backtest-bar-explanation-v1';

export type BarExplanationFill = {
	price: string;
	quantity: string;
	notional: string;
	fee: string;
	fee_rate: string;
};

export type BarExplanationExit = BarExplanationFill & {
	reason: string;
	gross_pnl: string;
	net_pnl: string;
	holding_bars: number;
};

export type BacktestBarExplanation = {
	candle_starts_at: string;
	entry_condition: 'matched' | 'not_matched' | 'undefined';
	exit_condition: 'matched' | 'not_matched' | 'undefined' | null;
	indicator_values: { indicator_id: string; value: string | null }[];
	entries: BarExplanationFill[];
	exits: BarExplanationExit[];
	equity: {
		cash: string;
		base_quantity: string;
		mark_price: string;
		equity: string;
	} | null;
};

export type BacktestBarExplanationPage = {
	schema_version: typeof BAR_EXPLANATION_SCHEMA;
	result_fingerprint: string;
	run_fingerprint: string;
	strategy_fingerprint: string;
	dataset_fingerprint: string;
	signal_trace_fingerprint: string;
	engine: string;
	product_id: string;
	timeframe: string;
	evaluation_start: string;
	evaluation_end: string;
	total_bars: number;
	limit: number;
	offset: number;
	returned: number;
	records: BacktestBarExplanation[];
	outside_trace: {
		candle_starts_at: string;
		kind: 'entry' | 'exit';
		fill: BarExplanationFill | BarExplanationExit;
	}[];
	next_cursor: string | null;
};

type ApiError = { detail?: { message?: string } };

export async function fetchBarExplanations(
	resultFingerprint: string,
	options: { limit?: number; cursor?: string | null; signal?: AbortSignal } = {}
): Promise<BacktestBarExplanationPage> {
	const params = new URLSearchParams();
	if (options.limit !== undefined) params.set('limit', String(options.limit));
	if (options.cursor) params.set('cursor', options.cursor);
	const query = params.toString();
	const response = await fetch(
		`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}/bar-explanations${query ? `?${query}` : ''}`,
		{ headers: { Accept: 'application/json' }, signal: options.signal }
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Bar explanations are unavailable.');
	}
	return (await response.json()) as BacktestBarExplanationPage;
}

export function outcomeLabel(outcome: string | null): string {
	if (outcome === null) return 'no signal-exit rule';
	if (outcome === 'matched') return 'matched';
	if (outcome === 'not_matched') return 'not matched';
	if (outcome === 'undefined') return 'undefined';
	return outcome;
}

export function indicatorText(values: { indicator_id: string; value: string | null }[]): string {
	return values
		.map((item) => `${item.indicator_id}=${item.value === null ? 'undefined' : item.value}`)
		.join(', ');
}

export function isExitFill(
	fill: BarExplanationFill | BarExplanationExit
): fill is BarExplanationExit {
	return 'reason' in fill;
}
