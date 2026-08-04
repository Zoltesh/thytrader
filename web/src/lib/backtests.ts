import { compareDecimalStrings, formatPercent as formatExactPercent } from './portfolio';

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
	total_spread_cost?: string | null;
};

export type EngineContractVersion = 'thytrader-bar-backtest-v1' | 'thytrader-bar-backtest-v2';

export type BrokerAssumptions = {
	price_model: 'constant_spread_bps';
	spread_bps: string;
	fill_policy: 'full';
	trigger_evaluation: 'bid_side';
	equity_marking: 'bid_close';
};

export type BacktestSummaryEntry = {
	result_fingerprint: string;
	run_fingerprint: string;
	strategy_fingerprint: string;
	dataset_fingerprint: string;
	engine_contract_version: EngineContractVersion;
	published_at: string;
	summary: BacktestSummary;
};

export type BacktestList = {
	entries: BacktestSummaryEntry[];
	limit: number;
	offset: number;
	returned: number;
};

export type BacktestFill = {
	candle_starts_at: string;
	price: string;
	quantity: string;
	notional: string;
	fee: string;
	fee_rate: string;
	reference_price?: string | null;
	executable_side?: 'ask' | 'bid' | 'mark' | null;
	spread_cost?: string | null;
};

export type BacktestTrade = {
	entry: BacktestFill;
	exit: BacktestFill & {
		reason: 'stop_loss' | 'take_profit' | 'time_exit' | 'evaluation_end';
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
	engine_contract_version: EngineContractVersion;
	broker?: BrokerAssumptions | null;
	run_fingerprint: string;
	strategy_fingerprint: string;
	dataset_fingerprint: string;
	signal_trace_fingerprint: string;
	trades: BacktestTrade[];
	equity_curve: EquityPoint[];
	summary: BacktestSummary;
};

export type BacktestDetail = {
	result: BacktestResult;
	result_fingerprint: string;
};

export type BacktestBenchmark = {
	benchmark_contract_version: 'thytrader-buy-and-hold-v1';
	benchmark_fingerprint: string;
	result_fingerprint: string;
	run_fingerprint: string;
	dataset_fingerprint: string;
	engine_contract_version: EngineContractVersion;
	broker?: BrokerAssumptions | null;
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

export type ApiError = {
	detail?: { code?: string; message?: string };
};

export function formatPercent(fraction: string): string {
	return formatExactPercent(fraction);
}

export function shortFingerprint(fingerprint: string): string {
	return `${fingerprint.slice(0, 16)}…${fingerprint.slice(-8)}`;
}

export function formatBrokerAssumptions(broker?: BrokerAssumptions | null): string {
	if (!broker) return 'Legacy V1 mark-price execution; no modeled spread evidence was recorded.';
	return `${broker.spread_bps} bps constant spread · ${broker.fill_policy} fills · ${broker.trigger_evaluation.replace('_', '-')} exits · ${broker.equity_marking.replace('_', '-')}`;
}

export async function fetchBacktests(signal?: AbortSignal): Promise<BacktestList> {
	const response = await fetch('/api/v1/backtests', {
		headers: { Accept: 'application/json' },
		signal
	});
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
	const response = await fetch(`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}`, {
		headers: { Accept: 'application/json' },
		signal
	});
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
