import {
	compareDecimalStrings,
	decimalChartGeometry,
	formatPercent as formatExactPercent,
	formatUsd,
	utcTimestampSeconds,
	type HonestLinePoint
} from './portfolio';

export { compareDecimalStrings };

const ENGINE_V1 = 'thytrader-bar-backtest-v1';
const ENGINE_V2 = 'thytrader-bar-backtest-v2';
const ENGINE_V3 = 'thytrader-bar-backtest-v3';

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

export type EngineContractVersion =
	'thytrader-bar-backtest-v1' | 'thytrader-bar-backtest-v2' | 'thytrader-bar-backtest-v3';

export type BrokerAssumptions = {
	price_model: 'constant_spread_bps' | 'post_only_limit' | (string & {});
	spread_bps: string;
	fill_policy: 'full' | 'resting_limit' | (string & {});
	trigger_evaluation: 'bid_side' | 'bar_extreme' | (string & {});
	equity_marking: 'bid_close' | 'last_close' | (string & {});
};

export type CostAssumptions = {
	maker_fee_rate?: string | null;
	taker_fee_rate?: string | null;
	fixed_slippage_bps?: string | null;
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
	fee_rate?: string | null;
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
	costs?: CostAssumptions | null;
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

function formatPolicyToken(value: string): string {
	return value.replaceAll('_', '-');
}

function formatDisplayFeeRate(rate: string): string {
	try {
		return formatPercent(rate);
	} catch {
		return rate;
	}
}

export function formatBrokerAssumptions(
	broker?: BrokerAssumptions | null,
	engineContractVersion?: string | null
): string {
	const engine = engineContractVersion ?? '';
	if (engine !== ENGINE_V1 && engine !== ENGINE_V2 && engine !== ENGINE_V3) {
		return engine
			? `Unknown engine contract ${engine}; broker assumptions are not labeled.`
			: 'Engine contract is missing; broker assumptions are not labeled.';
	}
	if (engine === ENGINE_V1) {
		if (broker) {
			return 'Unexpected broker block on a V1 result; broker assumptions are not labeled.';
		}
		return 'V1 mark-price execution; no modeled spread evidence was recorded.';
	}
	if (!broker) {
		return engine === ENGINE_V2
			? 'V2 requires disclosed broker assumptions; none were recorded.'
			: 'V3 requires disclosed post-only broker assumptions; none were recorded.';
	}
	if (engine === ENGINE_V2) {
		if (broker.price_model !== 'constant_spread_bps') {
			return `Unrecognized V2 price model ${broker.price_model}; broker assumptions are not labeled.`;
		}
		if (!isRecordedDecimal(broker.spread_bps)) {
			return 'V2 constant-spread model is missing spread_bps; a spread is not labeled.';
		}
		return `${broker.spread_bps} bps constant spread · ${formatPolicyToken(broker.fill_policy)} fills · ${formatPolicyToken(broker.trigger_evaluation)} exits · ${formatPolicyToken(broker.equity_marking)}`;
	}
	if (broker.price_model !== 'post_only_limit') {
		return `Unrecognized V3 price model ${broker.price_model}; broker assumptions are not labeled.`;
	}
	const fillPolicy = isRecordedDecimal(broker.fill_policy)
		? formatPolicyToken(broker.fill_policy)
		: 'unrecorded';
	const trigger = isRecordedDecimal(broker.trigger_evaluation)
		? formatPolicyToken(broker.trigger_evaluation)
		: 'unrecorded';
	const equity = isRecordedDecimal(broker.equity_marking)
		? formatPolicyToken(broker.equity_marking)
		: 'unrecorded';
	return `post-only limit · ${fillPolicy} fills · ${trigger} triggers · ${equity} marking`;
}

export function formatEngineFillAssumptions(engineContractVersion?: string | null): string {
	if (engineContractVersion === ENGINE_V1 || engineContractVersion === ENGINE_V2) {
		return 'Long-only, one position · completed close → next-open taker fill · adverse fixed slippage · time exit before intrabar exits · stop first if stop and target collide · terminal force close.';
	}
	if (engineContractVersion === ENGINE_V3) {
		return 'Long-only, one position · completed close rests a post-only buy at that close · later bar fills at the posted limit with maker fee and no modeled entry slippage · same-bar stop is a marketable taker fill · take-profit is not eligible on the fill bar · later bars may rest take-profit at the target with maker fee · time exit at close with taker fee · terminal force close.';
	}
	return engineContractVersion
		? `Unknown engine contract ${engineContractVersion}; this page will not invent fill semantics.`
		: 'Engine contract is missing; this page will not invent fill semantics.';
}

export function formatPublishedCosts(
	costs?: CostAssumptions | null,
	engineContractVersion?: string | null
): string {
	if (!costs) {
		return 'Published maker/taker fee rates and fixed_slippage_bps are not included in this response.';
	}
	const maker = isRecordedDecimal(costs.maker_fee_rate)
		? `maker ${formatDisplayFeeRate(costs.maker_fee_rate)}`
		: 'maker fee not recorded';
	const taker = isRecordedDecimal(costs.taker_fee_rate)
		? `taker ${formatDisplayFeeRate(costs.taker_fee_rate)}`
		: 'taker fee not recorded';
	const slippage = isRecordedDecimal(costs.fixed_slippage_bps)
		? `fixed slippage ${costs.fixed_slippage_bps} bps`
		: 'fixed_slippage_bps not recorded';
	const base = `${maker} · ${taker} · ${slippage} (published research-run CostAssumptions, not observed Coinbase fees)`;
	if (engineContractVersion === ENGINE_V3 && isRecordedDecimal(costs.fixed_slippage_bps)) {
		return `${base}. V3 modeled fills do not apply this slippage.`;
	}
	return base;
}

export function formatSpreadCostNote(
	engineContractVersion: string | null | undefined,
	totalSpreadCost: string | null | undefined
): string | null {
	if (engineContractVersion === ENGINE_V3) {
		if (!isRecordedDecimal(totalSpreadCost)) return null;
		return `Recorded spread cost: ${formatUsd(totalSpreadCost)}. V3 is not the constant-spread stress contract; this is not observed bid/ask data.`;
	}
	if (engineContractVersion === ENGINE_V2) {
		if (!isRecordedDecimal(totalSpreadCost)) {
			return 'Total modeled spread cost was not recorded on this result.';
		}
		return `Total modeled spread cost: ${formatUsd(totalSpreadCost)}. This is a disclosed stress assumption, not observed bid/ask data.`;
	}
	if (isRecordedDecimal(totalSpreadCost)) {
		return `Total modeled spread cost: ${formatUsd(totalSpreadCost)}. This is a disclosed stress assumption, not observed bid/ask data.`;
	}
	return null;
}

export function formatSameBarPolicy(engineContractVersion?: string | null): string {
	if (engineContractVersion === ENGINE_V1 || engineContractVersion === ENGINE_V2) {
		return 'Stop-first same-bar policy';
	}
	if (engineContractVersion === ENGINE_V3) {
		return 'Fill-bar stop; take-profit waits';
	}
	return 'Same-bar policy unlabeled';
}

export function formatFillFee(fill: Pick<BacktestFill, 'fee' | 'fee_rate'>): string {
	const amount = formatUsd(fill.fee);
	if (!isRecordedDecimal(fill.fee_rate)) {
		return `${amount} (fee rate not recorded)`;
	}
	return `${amount} (${formatDisplayFeeRate(fill.fee_rate)})`;
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
