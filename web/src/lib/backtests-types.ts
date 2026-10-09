/**
 * Backtest API shapes: list and summary entries, results with trades and equity,
 * diagnostics, evaluation windows, cost attribution, the buy-and-hold benchmark
 * and performance metrics. Re-exported by `backtests.ts`.
 */
import type { BacktestEngine, ValidityLimitCode } from './backtest-model';

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

/** Exact closed-trade amounts; before-fees PnL already includes spread and slippage. */
export type BacktestCostAttribution = {
	attribution_contract_version: 'thytrader-cost-attribution-v1';
	attribution_fingerprint: string;
	result_fingerprint: string;
	run_fingerprint: string;
	trade_count: number;
	fill_price_pnl_before_fees: string;
	entry_fees: string;
	exit_fees: string;
	net_pnl: string;
	accounting_residual: string;
	summary_net_pnl_delta: string;
};

export type BacktestDetail = {
	result: BacktestResult;
	result_fingerprint: string;
	costs?: CostAssumptions | null;
	diagnostics?: BacktestDiagnostics | null;
	window?: BacktestEvaluationWindow | null;
	cost_attribution?: BacktestCostAttribution | null;
};

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

export type BacktestListQuery = {
	limit?: number;
	offset?: number;
	strategy_fingerprint?: string;
	strategy_id?: string;
};
