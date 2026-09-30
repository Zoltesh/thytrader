import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';

/**
 * One canonical strategy definition (ADR 0082). A strategy is one mutable object:
 * there is no draft/published status and no version number. Backtests, studies,
 * and deployments snapshot the definition and record its `strategy_fingerprint`.
 */
export type StrategyDefinition = {
	strategy_id: string;
	name: string;
	description: string | null;
	created_at: string;
	sizing: {
		kind?: string;
		risk_fraction: string;
		min_quote_notional: string;
		max_quote_notional: string;
	};
	portfolio_limits: { max_strategy_exposure_fraction: string; max_concurrent_positions?: number };
	[key: string]: unknown;
};

/** Raw saved document: any JSON object (it may be an invalid work in progress). */
export type StrategyDocument = { [key: string]: unknown };

export type StrategyValidationIssue = { loc: string; message: string };

export type StrategyValidation = { valid: boolean; issues: StrategyValidationIssue[] };

/** `GET/PUT /api/v1/strategies/{id}` and the create / clone / import responses. */
export type StrategyRecord = {
	strategy_id: string;
	name: string;
	revision: number;
	created_at: string;
	updated_at: string;
	document: StrategyDocument;
	/** Present only when the saved document is a valid definition. */
	strategy: StrategyDefinition | null;
	validation: StrategyValidation;
	/** Fingerprint the next snapshot gets; null while the definition is invalid. */
	current_fingerprint: string | null;
	summary: string | null;
	product_id: string | null;
	timeframe: string | null;
};

export type StrategyLibraryBacktest = {
	result_fingerprint: string;
	strategy_fingerprint?: string;
	published_at: string;
	summary: {
		initial_equity: string;
		final_equity: string;
		total_return_fraction: string;
		trade_count: number;
		win_rate: string;
		maximum_drawdown_fraction: string;
		[key: string]: unknown;
	};
};

export type StrategyLibraryPaperLive = { paper: string; live: string };

export type StrategyLibraryEntry = {
	strategy_id: string;
	name: string;
	product_id: string | null;
	timeframe: string | null;
	revision: number;
	valid: boolean;
	current_fingerprint: string | null;
	summary: string | null;
	created_at: string;
	updated_at: string;
	backtest: StrategyLibraryBacktest | null;
	paper_live: StrategyLibraryPaperLive;
	active_deployment_count: number;
};

/** What deleting one strategy removes (and the live history it keeps). */
export type StrategyDeletionCounts = {
	snapshots: number;
	backtests: number;
	research_runs: number;
	studies: number;
	research_jobs: number;
	dataset_bindings: number;
	paper_deployments: number;
	live_deployments_kept: number;
	allocations_removed: number;
};

export type StrategyDeletionResult = {
	strategy_id: string;
	name: string;
	outcome: 'deleted';
	counts: StrategyDeletionCounts;
	risk_policy_republished: boolean;
};

export type BulkDeleteOutcome = 'deleted' | 'would_delete' | 'blocked' | 'not_found' | 'failed';

export type BulkDeleteItem = {
	strategy_id: string;
	name: string | null;
	outcome: BulkDeleteOutcome;
	code: string | null;
	message: string | null;
	deployment_ids: string[];
	counts: StrategyDeletionCounts | null;
};

export type BulkDeleteResponse = {
	dry_run: boolean;
	results: BulkDeleteItem[];
	would_delete?: number;
	deleted: number;
	blocked: number;
	not_found: number;
	failed: number;
};

/** `GET /api/v1/strategies/snapshots/{fingerprint}`. */
export type StrategySnapshot = {
	strategy_fingerprint: string;
	strategy_id: string | null;
	strategy_name: string | null;
	strategy: StrategyDefinition;
	created_at: string;
	is_current: boolean;
};

export type IdentityInput = 'open' | 'high' | 'low' | 'close' | 'volume';

export type IndicatorInput =
	IdentityInput | ['high', 'low', 'close'] | ['high', 'low', 'close', 'volume'];

export type IndicatorKindValue =
	| 'ema'
	| 'sma'
	| 'rsi'
	| 'atr'
	| 'volume_sma'
	| 'highest'
	| 'lowest'
	| 'stdev'
	| 'stdev_sample'
	| 'roc'
	| 'williams_r'
	| 'cci'
	| 'wma'
	| 'momentum'
	| 'mfi'
	| 'macd'
	| 'bollinger'
	| 'stochastic'
	| 'adx'
	| 'identity'
	| 'constant';

export const INDICATOR_KIND_OPTIONS: readonly { kind: IndicatorKindValue; label: string }[] = [
	{ kind: 'ema', label: 'EMA' },
	{ kind: 'sma', label: 'SMA' },
	{ kind: 'rsi', label: 'RSI' },
	{ kind: 'atr', label: 'ATR' },
	{ kind: 'volume_sma', label: 'Volume SMA' },
	{ kind: 'highest', label: 'Highest' },
	{ kind: 'lowest', label: 'Lowest' },
	{ kind: 'stdev', label: 'Stdev' },
	{ kind: 'stdev_sample', label: 'Sample stdev' },
	{ kind: 'roc', label: 'ROC' },
	{ kind: 'williams_r', label: 'Williams %R' },
	{ kind: 'cci', label: 'CCI' },
	{ kind: 'wma', label: 'WMA' },
	{ kind: 'momentum', label: 'Momentum' },
	{ kind: 'mfi', label: 'MFI' },
	{ kind: 'macd', label: 'MACD' },
	{ kind: 'bollinger', label: 'Bollinger' },
	{ kind: 'stochastic', label: 'Stochastic' },
	{ kind: 'adx', label: 'ADX' },
	{ kind: 'identity', label: 'OHLCV' },
	{ kind: 'constant', label: 'Constant' }
];

export const INDICATOR_OUTPUT_SERIES: Readonly<
	Partial<Record<IndicatorKindValue, readonly string[]>>
> = {
	macd: ['macd', 'signal', 'histogram'],
	bollinger: ['middle', 'upper', 'lower'],
	stochastic: ['k', 'd'],
	adx: ['adx', 'plus_di', 'minus_di']
};

export const IDENTITY_INPUT_OPTIONS: readonly { value: IdentityInput; label: string }[] = [
	{ value: 'open', label: 'Open' },
	{ value: 'high', label: 'High' },
	{ value: 'low', label: 'Low' },
	{ value: 'close', label: 'Close' },
	{ value: 'volume', label: 'Volume' }
];

const IDENTITY_INPUTS: readonly IdentityInput[] = IDENTITY_INPUT_OPTIONS.map(
	(option) => option.value
);

export type IndicatorDraft = {
	id: string;
	kind: IndicatorKindValue;
	input?: IndicatorInput;
	timeframe?: string;
	parameters: {
		period?: number;
		value?: string;
		fast_period?: number;
		slow_period?: number;
		signal_period?: number;
		stdev_multiplier?: string;
		k_period?: number;
		d_period?: number;
	};
};

export type ComparisonOperatorValue =
	| 'greater_than'
	| 'greater_than_or_equal'
	| 'less_than'
	| 'less_than_or_equal'
	| 'equals'
	| 'crosses_above'
	| 'crosses_below';

export type OperandDraft = { indicator: string; series?: string } | { literal: string };

export type ConditionDraft =
	| { left: OperandDraft; operator: ComparisonOperatorValue; right: OperandDraft }
	| { all: ConditionDraft[] }
	| { any: ConditionDraft[] }
	| { not: ConditionDraft };

export type HtfFilterDraft = {
	timeframe: string;
	warmup_bars: number;
	indicators: IndicatorDraft[];
	when: ConditionDraft;
};

export type CoveredInstrumentDraft = {
	product_id: string;
	base_currency: string;
	quote_currency?: string;
};

export type PyramidingDraft = {
	enabled: true;
	require_unrealized_profit: true;
};

export type BuilderModel = {
	strategy_id: string;
	revision: number;
	name: string;
	description: string;
	created_at: string;
	product_id: string;
	base_currency: string;
	additional_instruments: CoveredInstrumentDraft[];
	timeframe: string;
	warmup_bars: number;
	indicators: IndicatorDraft[];
	htf_filter: HtfFilterDraft | null;
	entry: { when: ConditionDraft };
	side: 'long' | 'short';
	sizing: { risk_fraction: string; min_quote_notional: string; max_quote_notional: string };
	portfolio_limits: { max_strategy_exposure_fraction: string; max_concurrent_positions: number };
	exits: {
		initial_stop: { kind: string; atr_indicator: string; multiple: string };
		take_profit: { kind: string; multiple: string };
		trailing_stop:
			| { enabled: false }
			| { enabled: true; kind: 'atr_multiple'; atr_indicator: string; multiple: string };
		time_exit: { max_bars_held: number };
	};
	execution: {
		entry_preference: string;
		max_entry_wait_bars: number;
		on_unfilled_entry: string;
	};
	cooldown_bars: number;
	max_open_positions: number;
	pyramiding: PyramidingDraft | null;
	metadata: { tags: string[]; notes: string[] };
};

export type Dataset = {
	product_id: string;
	timeframe: string;
	starts_at: string;
	ends_at: string;
	content_fingerprint: string;
};

/** Duration-ascending legal strategy, paper, live, discretionary, and HTF tokens. */
export const EXECUTION_TIMEFRAMES = [
	'1m',
	'5m',
	'15m',
	'30m',
	'1h',
	'2h',
	'4h',
	'6h',
	'1d'
] as const;

export type ExecutionTimeframe = (typeof EXECUTION_TIMEFRAMES)[number];

const TIMEFRAME_SECONDS: Record<ExecutionTimeframe, number> = {
	'1m': 60,
	'5m': 300,
	'15m': 900,
	'30m': 1_800,
	'1h': 3_600,
	'2h': 7_200,
	'4h': 14_400,
	'6h': 21_600,
	'1d': 86_400
};

const CONFIGURABLE_ROLLING_KINDS: ReadonlySet<IndicatorKindValue> = new Set([
	'ema',
	'sma',
	'wma',
	'highest',
	'lowest',
	'stdev',
	'stdev_sample',
	'roc',
	'momentum'
]);

function defaultRollingInput(kind: IndicatorKindValue): IdentityInput {
	if (kind === 'highest') return 'high';
	if (kind === 'lowest') return 'low';
	return 'close';
}

function lockedIndicatorInput(kind: IndicatorKindValue): IndicatorInput {
	if (
		kind === 'atr' ||
		kind === 'williams_r' ||
		kind === 'cci' ||
		kind === 'stochastic' ||
		kind === 'adx'
	) {
		return ['high', 'low', 'close'];
	}
	if (kind === 'mfi') return ['high', 'low', 'close', 'volume'];
	if (kind === 'volume_sma') return 'volume';
	if (CONFIGURABLE_ROLLING_KINDS.has(kind)) return defaultRollingInput(kind);
	return 'close';
}

function selectedRollingInput(indicator: IndicatorDraft): IdentityInput {
	return typeof indicator.input === 'string' && IDENTITY_INPUTS.includes(indicator.input)
		? indicator.input
		: defaultRollingInput(indicator.kind);
}

export function isConfigurableRollingKind(kind: IndicatorKindValue): boolean {
	return CONFIGURABLE_ROLLING_KINDS.has(kind);
}

function indicatorPeriodMax(kind: IndicatorKindValue): number {
	return kind === 'rsi' ||
		kind === 'atr' ||
		kind === 'williams_r' ||
		kind === 'cci' ||
		kind === 'mfi' ||
		kind === 'adx'
		? 100
		: 500;
}

function clampPeriod(value: number | undefined, maximum: number, fallback: number): number {
	if (typeof value === 'number' && Number.isInteger(value) && value >= 2) {
		return Math.min(value, maximum);
	}
	return fallback;
}

/** Build a comparison operand for the first declared indicator, including series when required. */
export function defaultIndicatorOperand(indicators: IndicatorDraft[]): OperandDraft {
	const indicator = indicators[0];
	if (indicator === undefined) return { indicator: 'fast' };
	return indicatorOperand(indicator, INDICATOR_OUTPUT_SERIES[indicator.kind]?.[0]);
}

/** Encode one indicator (and optional series) as a builder select key. */
export function indicatorOperandKey(operand: { indicator: string; series?: string }): string {
	return operand.series === undefined
		? `indicator:${operand.indicator}`
		: `indicator:${operand.indicator}.${operand.series}`;
}

/** Parse a builder select key into an indicator operand, or null for literals. */
export function parseIndicatorOperandKey(key: string): OperandDraft | null {
	if (!key.startsWith('indicator:')) return null;
	const rest = key.slice('indicator:'.length);
	const separator = rest.indexOf('.');
	if (separator === -1) return { indicator: rest };
	return { indicator: rest.slice(0, separator), series: rest.slice(separator + 1) };
}

function indicatorOperand(indicator: IndicatorDraft, series: string | undefined): OperandDraft {
	if (series === undefined) return { indicator: indicator.id };
	return { indicator: indicator.id, series };
}

/** Expand multi-series kinds into one selectable operand per output. */
export function operandChoices(indicators: IndicatorDraft[]): { key: string; label: string }[] {
	const choices: { key: string; label: string }[] = [];
	for (const indicator of indicators) {
		const series = INDICATOR_OUTPUT_SERIES[indicator.kind];
		if (series === undefined) {
			choices.push({ key: `indicator:${indicator.id}`, label: indicator.id });
			continue;
		}
		for (const name of series) {
			choices.push({
				key: `indicator:${indicator.id}.${name}`,
				label: `${indicator.id}.${name}`
			});
		}
	}
	choices.push({ key: 'literal', label: 'literal value' });
	return choices;
}

/** Align one builder indicator with the kind's locked input and parameter shape. */
export function applyIndicatorKindDefaults(indicator: IndicatorDraft): void {
	if (indicator.kind === 'identity') {
		indicator.input = IDENTITY_INPUTS.includes(indicator.input as IdentityInput)
			? (indicator.input as IdentityInput)
			: 'close';
		indicator.parameters = {};
		return;
	}
	if (indicator.kind === 'constant') {
		const previous = indicator.parameters.value;
		delete indicator.input;
		delete indicator.timeframe;
		indicator.parameters = {
			value: previous !== undefined && previous.length > 0 ? previous : '50'
		};
		return;
	}
	if (indicator.kind === 'macd') {
		indicator.input = 'close';
		const fast = clampPeriod(indicator.parameters.fast_period, 500, 12);
		const slow = clampPeriod(indicator.parameters.slow_period, 500, 26);
		indicator.parameters = {
			fast_period: fast,
			slow_period: slow > fast ? slow : Math.min(500, fast + 1),
			signal_period: clampPeriod(indicator.parameters.signal_period, 500, 9)
		};
		return;
	}
	if (indicator.kind === 'bollinger') {
		indicator.input = 'close';
		const previousMultiplier = indicator.parameters.stdev_multiplier;
		indicator.parameters = {
			period: clampPeriod(indicator.parameters.period, 500, 20),
			stdev_multiplier:
				previousMultiplier !== undefined && previousMultiplier.length > 0 ? previousMultiplier : '2'
		};
		return;
	}
	if (indicator.kind === 'stochastic') {
		indicator.input = ['high', 'low', 'close'];
		indicator.parameters = {
			k_period: clampPeriod(indicator.parameters.k_period, 100, 14),
			d_period: clampPeriod(indicator.parameters.d_period, 500, 3)
		};
		return;
	}
	if (isConfigurableRollingKind(indicator.kind)) {
		indicator.input = selectedRollingInput(indicator);
		const previousPeriod = indicator.parameters.period;
		const maximum = indicatorPeriodMax(indicator.kind);
		const period =
			typeof previousPeriod === 'number' && Number.isInteger(previousPeriod) && previousPeriod >= 2
				? Math.min(previousPeriod, maximum)
				: 50;
		indicator.parameters = { period };
		return;
	}
	indicator.input = lockedIndicatorInput(indicator.kind);
	const previousPeriod = indicator.parameters.period;
	const maximum = indicatorPeriodMax(indicator.kind);
	const period =
		typeof previousPeriod === 'number' && Number.isInteger(previousPeriod) && previousPeriod >= 2
			? Math.min(previousPeriod, maximum)
			: 50;
	indicator.parameters = { period };
}

/** Canonical indicator payload for saving a strategy. */
export function serializeIndicator(
	indicator: IndicatorDraft,
	decisionTimeframe?: string
): IndicatorDraft {
	const extraTimeframe =
		decisionTimeframe !== undefined &&
		indicator.kind !== 'constant' &&
		indicator.timeframe !== undefined &&
		indicator.timeframe !== '' &&
		indicator.timeframe !== decisionTimeframe
			? indicator.timeframe
			: undefined;
	if (indicator.kind === 'constant') {
		return {
			id: indicator.id,
			kind: 'constant',
			parameters: { value: indicator.parameters.value ?? '0' }
		};
	}
	if (indicator.kind === 'identity') {
		return {
			id: indicator.id,
			kind: 'identity',
			input: IDENTITY_INPUTS.includes(indicator.input as IdentityInput)
				? (indicator.input as IdentityInput)
				: 'close',
			parameters: {},
			...(extraTimeframe === undefined ? {} : { timeframe: extraTimeframe })
		};
	}
	if (indicator.kind === 'macd') {
		return {
			id: indicator.id,
			kind: 'macd',
			input: 'close',
			parameters: {
				fast_period: indicator.parameters.fast_period ?? 12,
				slow_period: indicator.parameters.slow_period ?? 26,
				signal_period: indicator.parameters.signal_period ?? 9
			},
			...(extraTimeframe === undefined ? {} : { timeframe: extraTimeframe })
		};
	}
	if (indicator.kind === 'bollinger') {
		return {
			id: indicator.id,
			kind: 'bollinger',
			input: 'close',
			parameters: {
				period: indicator.parameters.period ?? 20,
				stdev_multiplier: indicator.parameters.stdev_multiplier ?? '2'
			},
			...(extraTimeframe === undefined ? {} : { timeframe: extraTimeframe })
		};
	}
	if (indicator.kind === 'stochastic') {
		return {
			id: indicator.id,
			kind: 'stochastic',
			input: ['high', 'low', 'close'],
			parameters: {
				k_period: indicator.parameters.k_period ?? 14,
				d_period: indicator.parameters.d_period ?? 3
			},
			...(extraTimeframe === undefined ? {} : { timeframe: extraTimeframe })
		};
	}
	if (isConfigurableRollingKind(indicator.kind)) {
		return {
			id: indicator.id,
			kind: indicator.kind,
			input: selectedRollingInput(indicator),
			parameters: { period: indicator.parameters.period ?? 2 },
			...(extraTimeframe === undefined ? {} : { timeframe: extraTimeframe })
		};
	}
	return {
		id: indicator.id,
		kind: indicator.kind,
		input: indicator.input ?? lockedIndicatorInput(indicator.kind),
		parameters: { period: indicator.parameters.period ?? 2 },
		...(extraTimeframe === undefined ? {} : { timeframe: extraTimeframe })
	};
}

/**
 * Return HTF clocks that are strictly coarser integer multiples of the LTF decision clock.
 */
export function validHtfTimeframes(decisionTimeframe: string): string[] {
	const decisionSeconds = TIMEFRAME_SECONDS[decisionTimeframe as ExecutionTimeframe];
	if (decisionSeconds === undefined) return [];
	return EXECUTION_TIMEFRAMES.filter((timeframe) => {
		const seconds = TIMEFRAME_SECONDS[timeframe];
		return seconds > decisionSeconds && seconds % decisionSeconds === 0;
	});
}

/**
 * Return the clock one LTF-list indicator evaluates on.
 */
export function resolvedIndicatorTimeframe(
	indicator: IndicatorDraft,
	decisionTimeframe: string
): string {
	if (indicator.timeframe === undefined || indicator.timeframe === '') {
		return decisionTimeframe;
	}
	return indicator.timeframe;
}

/**
 * Return extra LTF-list clocks in venue-duration order.
 */
export function extraIndicatorTimeframes(
	indicators: IndicatorDraft[],
	decisionTimeframe: string
): string[] {
	const clocks = new Set<string>();
	for (const indicator of indicators) {
		const clock = resolvedIndicatorTimeframe(indicator, decisionTimeframe);
		if (clock !== decisionTimeframe) clocks.add(clock);
	}
	return EXECUTION_TIMEFRAMES.filter((timeframe) => clocks.has(timeframe));
}

/**
 * Return extra LTF-list clocks that need their own research dataset fingerprint.
 */
export function unboundIndicatorTimeframes(
	indicators: IndicatorDraft[],
	decisionTimeframe: string,
	htfTimeframe: string | null | undefined
): string[] {
	return extraIndicatorTimeframes(indicators, decisionTimeframe).filter(
		(timeframe) => timeframe !== htfTimeframe
	);
}

/**
 * Seed a conservative HTF trend filter when the operator enables the optional block.
 */
export function defaultHtfFilter(decisionTimeframe: string): HtfFilterDraft {
	const timeframes = validHtfTimeframes(decisionTimeframe);
	const timeframe = timeframes.includes('1h') ? '1h' : (timeframes[0] ?? '6h');
	return {
		timeframe,
		warmup_bars: 50,
		indicators: [
			{ id: 'htf_ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
			{ id: 'htf_ema_slow', kind: 'ema', input: 'close', parameters: { period: 50 } }
		],
		when: {
			all: [
				{
					left: { indicator: 'htf_ema_fast' },
					operator: 'greater_than',
					right: { indicator: 'htf_ema_slow' }
				}
			]
		}
	};
}

/**
 * Collapse verified cumulative revisions to one latest dataset per product and timeframe.
 * Every revision of a product/timeframe shares its start and grows its end, so the
 * newest `ends_at` (tiebroken by earlier `starts_at`) is a strict superset.
 */
export function latestDatasets(datasets: Dataset[]): Dataset[] {
	const byMarket = new Map<string, Dataset>();
	for (const dataset of datasets) {
		const key = `${dataset.product_id}:${dataset.timeframe}`;
		const current = byMarket.get(key);
		if (
			current === undefined ||
			dataset.ends_at > current.ends_at ||
			(dataset.ends_at === current.ends_at && dataset.starts_at < current.starts_at)
		) {
			byMarket.set(key, dataset);
		}
	}
	return [...byMarket.values()];
}

/**
 * Parse one zone-less `datetime-local` input value as the UTC instant it
 * represents. The launch form labels both fields UTC, so local-time
 * interpretation would silently shift the identity-bearing evaluation window.
 */
export function parseUtcInputValue(value: string): Date {
	return new Date(`${value}Z`);
}

/** Format one instant as a zone-less `datetime-local` string in UTC. */
export function formatUtcInputValue(instant: Date): string {
	return instant.toISOString().slice(0, 16);
}

/**
 * Compute the inclusive evaluation window one dataset can support for a warmup.
 * The dataset must supply `warmupBars` completed candles before the window and
 * one candle after it for the final next-open fill.
 */
export function datasetEvaluationWindow(
	dataset: Dataset,
	warmupBars: number,
	timeframe: string = '1h'
): { min: string; max: string } {
	const seconds = TIMEFRAME_SECONDS[timeframe as ExecutionTimeframe] ?? 3_600;
	const barMs = seconds * 1_000;
	return {
		min: formatUtcInputValue(new Date(new Date(dataset.starts_at).getTime() + warmupBars * barMs)),
		max: formatUtcInputValue(new Date(new Date(dataset.ends_at).getTime() - barMs))
	};
}

/**
 * Describe the inclusive UTC evaluation window a dataset can support.
 * Names the strategy timeframe so 5m windows are not labeled as hours.
 */
export function researchWindowHint(
	bounds: { min: string; max: string },
	warmupBars: number,
	timeframe: string
): string {
	const barTimeframe = timeframe.trim() === '' ? '1h' : timeframe;
	return `Usable window for this dataset: ${bounds.min.replace('T', ' ')} → ${bounds.max.replace('T', ' ')} (UTC, ${barTimeframe} bars). It must fit inside the dataset with ${warmupBars} warmup bars before it and one candle after it.`;
}

type StrategyLibraryResponse = {
	strategies: StrategyLibraryEntry[];
	total?: number;
	has_more?: boolean;
	next_cursor?: string | null;
};
type BacktestSubmission = {
	run_fingerprint: string;
	result_fingerprint: string;
	strategy_id?: string;
	strategy_fingerprint?: string;
};

/**
 * One failed strategy API call. `code` is the structured `detail.code` when the
 * server sent one (for example `strategy_revision_conflict`, `strategy_invalid`,
 * `strategy_has_active_deployments`), so callers can branch without parsing text.
 */
export class StrategyApiError extends Error {
	readonly status: number;
	readonly code: string | null;
	readonly detail: Record<string, unknown>;

	constructor(
		status: number,
		code: string | null,
		message: string,
		detail: Record<string, unknown>
	) {
		super(message);
		this.name = 'StrategyApiError';
		this.status = status;
		this.code = code;
		this.detail = detail;
	}
}

/** Structured error code of a caught value, or null. */
export function strategyErrorCode(caught: unknown): string | null {
	return caught instanceof StrategyApiError ? caught.code : null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const method = init?.method?.toUpperCase() ?? 'GET';
	if (method !== 'GET' && method !== 'HEAD') {
		await ensureBrowserCsrfSession();
	}
	const response = await fetch(url, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...init?.headers,
			...mutationHeaders()
		}
	});
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as {
			detail?: string | ({ message?: string; code?: string } & Record<string, unknown>);
		};
		const detail =
			typeof body.detail === 'string'
				? body.detail
				: (body.detail?.message ?? 'no details returned');
		const structured = typeof body.detail === 'object' && body.detail !== null ? body.detail : {};
		const code = typeof structured.code === 'string' ? structured.code : null;
		throw new StrategyApiError(
			response.status,
			code,
			`The research operation failed (HTTP ${response.status}): ${detail}`,
			structured
		);
	}
	return (await response.json()) as T;
}

function strategyPath(strategyId: string, suffix = ''): string {
	return `/api/v1/strategies/${encodeURIComponent(strategyId)}${suffix}`;
}

/** Create a strategy from a fail-closed research template. */
export async function createStrategy(options?: {
	template?: string;
	product_id?: string;
	timeframe?: string;
}): Promise<StrategyRecord> {
	const params = new URLSearchParams();
	if (options?.template !== undefined && options.template !== 'ema-trend') {
		params.set('template', options.template);
	}
	if (options?.product_id !== undefined && options.product_id !== 'BTC-USD') {
		params.set('product_id', options.product_id);
	}
	if (options?.timeframe !== undefined && options.timeframe !== '1h') {
		params.set('timeframe', options.timeframe);
	}
	const query = params.toString();
	const path = query === '' ? '/api/v1/strategies' : `/api/v1/strategies?${query}`;
	return request<StrategyRecord>(path, { method: 'POST' });
}

export async function fetchStrategy(strategyId: string): Promise<StrategyRecord> {
	return request<StrategyRecord>(strategyPath(strategyId));
}

/**
 * Save the document in place. The revision guard rejects a stale save with 409
 * `strategy_revision_conflict`; it never overwrites. Invalid documents are saved
 * and come back with their validation result.
 */
export async function saveStrategy(
	strategyId: string,
	document: StrategyDocument,
	revision: number
): Promise<StrategyRecord> {
	return request<StrategyRecord>(strategyPath(strategyId), {
		method: 'PUT',
		body: JSON.stringify({ document, revision })
	});
}

export async function deleteStrategy(strategyId: string): Promise<StrategyDeletionResult> {
	return request<StrategyDeletionResult>(strategyPath(strategyId), { method: 'DELETE' });
}

/** Preview (`dryRun`) or confirm deletion of up to 100 strategies; results are per strategy. */
export async function bulkDeleteStrategies(
	strategyIds: string[],
	options: { dryRun: boolean }
): Promise<BulkDeleteResponse> {
	return request<BulkDeleteResponse>('/api/v1/strategies/bulk-delete', {
		method: 'POST',
		body: JSON.stringify({
			strategy_ids: strategyIds,
			confirm: !options.dryRun,
			dry_run: options.dryRun
		})
	});
}

export async function cloneStrategy(strategyId: string): Promise<StrategyRecord> {
	return request<StrategyRecord>(strategyPath(strategyId, '/clone'), { method: 'POST' });
}

/** Import one JSON document as a new strategy (always a fresh identity). */
export async function importStrategy(document: unknown): Promise<StrategyRecord> {
	return request<StrategyRecord>('/api/v1/strategies/import', {
		method: 'POST',
		body: JSON.stringify({ document })
	});
}

/** The exact rules one run or bot used, addressed by snapshot fingerprint. */
export async function fetchStrategySnapshot(fingerprint: string): Promise<StrategySnapshot> {
	return request<StrategySnapshot>(
		`/api/v1/strategies/snapshots/${encodeURIComponent(fingerprint)}`
	);
}

export async function fetchStrategyPage(
	limit: 10 | 25 | 50 | 100,
	cursor?: string
): Promise<{ entries: StrategyLibraryEntry[]; nextCursor: string | null; total: number | null }> {
	const params = new URLSearchParams({ limit: String(limit) });
	if (cursor !== undefined) params.set('cursor', cursor);
	const body = await request<StrategyLibraryResponse>(`/api/v1/strategies?${params.toString()}`);
	const hasMore = body.has_more === true;
	if (hasMore && body.strategies.length === 0) {
		throw new Error('Strategy library returned an empty page while claiming more strategies.');
	}
	if (hasMore && !body.next_cursor) {
		throw new Error('Strategy library has more strategies but no next cursor.');
	}
	return {
		entries: body.strategies,
		nextCursor: hasMore ? body.next_cursor! : null,
		total: typeof body.total === 'number' ? body.total : null
	};
}

export async function listStrategies(
	onPage?: (entries: StrategyLibraryEntry[], hasMore: boolean) => void
): Promise<StrategyLibraryEntry[]> {
	const rows: StrategyLibraryEntry[] = [];
	let cursor: string | undefined;
	for (let page = 0; page < 50; page += 1) {
		const params = new URLSearchParams({ limit: '100' });
		if (cursor !== undefined) params.set('cursor', cursor);
		const body = await request<StrategyLibraryResponse>(`/api/v1/strategies?${params.toString()}`);
		const hasMore = body.has_more === true;
		const nextCursor = body.next_cursor;
		if (hasMore && body.strategies.length === 0) {
			throw new Error('Strategy library returned an empty page while claiming more strategies.');
		}
		if (hasMore && !nextCursor) {
			throw new Error('Strategy library has more strategies but no next cursor.');
		}
		rows.push(...body.strategies);
		onPage?.([...rows], hasMore);
		if (!hasMore) return rows;
		cursor = nextCursor ?? undefined;
	}
	throw new Error('Strategy library truncated: exceeded the 50-page fetch cap.');
}

function toHtfFilterDraft(raw: unknown): HtfFilterDraft | null {
	if (raw === null || raw === undefined || typeof raw !== 'object') return null;
	const filter = raw as {
		timeframe?: string;
		data_requirements?: { warmup_bars?: number };
		indicators?: IndicatorDraft[];
		when?: ConditionDraft;
	};
	if (filter.timeframe === undefined || filter.when === undefined) return null;
	return {
		timeframe: filter.timeframe,
		warmup_bars: filter.data_requirements?.warmup_bars ?? 50,
		indicators: filter.indicators ?? [],
		when: filter.when
	};
}

export function toBuilderModel(strategy: StrategyDefinition, revision: number): BuilderModel {
	const entry = strategy.entry as {
		when: ConditionDraft;
		cooldown_bars: number;
		side?: 'long' | 'short';
		max_open_positions?: number;
		pyramiding?: PyramidingDraft | null;
	};
	const extras = (strategy.additional_instruments as CoveredInstrumentDraft[] | undefined) ?? [];
	const exits = strategy.exits as BuilderModel['exits'];
	return {
		strategy_id: strategy.strategy_id,
		revision,
		name: strategy.name,
		description: strategy.description ?? '',
		created_at: strategy.created_at,
		product_id: (strategy.instrument as { product_id: string }).product_id,
		base_currency: (strategy.instrument as { base_currency: string }).base_currency,
		additional_instruments: extras.map((item) => ({
			product_id: item.product_id,
			base_currency: item.base_currency,
			quote_currency: quoteCurrencyFor(item.product_id, item.quote_currency ?? 'USD')
		})),
		timeframe: strategy.timeframe as string,
		warmup_bars: (strategy.data_requirements as { warmup_bars: number }).warmup_bars,
		indicators: ((strategy.indicators as IndicatorDraft[]) ?? []).map((indicator) => ({
			...indicator,
			timeframe: indicator.timeframe ?? ''
		})),
		htf_filter: toHtfFilterDraft(strategy.htf_filter),
		entry: { when: entry.when },
		side: entry.side === 'short' ? 'short' : 'long',
		sizing: {
			risk_fraction: strategy.sizing.risk_fraction,
			min_quote_notional: strategy.sizing.min_quote_notional,
			max_quote_notional: strategy.sizing.max_quote_notional
		},
		portfolio_limits: {
			max_strategy_exposure_fraction: strategy.portfolio_limits.max_strategy_exposure_fraction,
			max_concurrent_positions: strategy.portfolio_limits.max_concurrent_positions ?? 1
		},
		exits,
		execution: strategy.execution as BuilderModel['execution'],
		cooldown_bars: entry.cooldown_bars,
		max_open_positions: entry.max_open_positions ?? 1,
		pyramiding: entry.pyramiding ?? null,
		metadata: strategy.metadata as BuilderModel['metadata']
	};
}

/**
 * Quote currency of a Coinbase spot product id (`BASE-QUOTE`). The backend requires
 * `product_id == base-quote`, so the quote is always derived from the id; `fallback`
 * applies only when the id does not have that shape.
 */
export function quoteCurrencyFor(productId: string, fallback = 'USD'): string {
	const parts = productId.split('-');
	return parts.length === 2 && parts[1].length > 0 ? parts[1] : fallback;
}

export function fromBuilderModel(model: BuilderModel): StrategyDefinition {
	return {
		schema_version: '1.0',
		strategy_id: model.strategy_id,
		name: model.name,
		description: model.description.trim().length > 0 ? model.description : null,
		created_at: model.created_at,
		instrument: {
			product_id: model.product_id,
			base_currency: model.base_currency,
			quote_currency: quoteCurrencyFor(model.product_id)
		},
		...(model.additional_instruments.length === 0
			? {}
			: {
					additional_instruments: model.additional_instruments.map((item) => ({
						product_id: item.product_id,
						base_currency: item.base_currency,
						quote_currency: quoteCurrencyFor(item.product_id, item.quote_currency ?? 'USD')
					}))
				}),
		timeframe: model.timeframe,
		data_requirements: {
			warmup_bars: model.warmup_bars,
			required_fields: ['open', 'high', 'low', 'close', 'volume']
		},
		indicators: model.indicators.map((indicator) => serializeIndicator(indicator, model.timeframe)),
		...(model.htf_filter === null
			? {}
			: {
					htf_filter: {
						timeframe: model.htf_filter.timeframe,
						data_requirements: {
							warmup_bars: model.htf_filter.warmup_bars,
							required_fields: ['open', 'high', 'low', 'close', 'volume']
						},
						indicators: model.htf_filter.indicators.map((indicator) =>
							serializeIndicator(indicator)
						),
						when: model.htf_filter.when
					}
				}),
		entry: {
			side: model.side,
			when: model.entry.when,
			cooldown_bars: model.cooldown_bars,
			max_open_positions: model.max_open_positions,
			...(model.pyramiding === null ? {} : { pyramiding: model.pyramiding })
		},
		sizing: { kind: 'risk_fraction', ...model.sizing },
		portfolio_limits: {
			max_strategy_exposure_fraction: model.portfolio_limits.max_strategy_exposure_fraction,
			max_concurrent_positions: model.portfolio_limits.max_concurrent_positions
		},
		exits: model.exits,
		execution: model.execution,
		metadata: model.metadata
	};
}

export async function listDatasets(): Promise<Dataset[]> {
	return (await request<{ datasets: Dataset[] }>('/api/v1/market-data/datasets/latest')).datasets;
}

/**
 * Builder model for a saved document, or null when the document cannot be
 * projected into the form (an invalid work in progress that is missing blocks).
 */
export function builderModelFromRecord(record: StrategyRecord): BuilderModel | null {
	const source = (record.strategy ?? record.document) as StrategyDefinition;
	try {
		const model = toBuilderModel(source, record.revision);
		if (typeof model.name !== 'string' || typeof model.product_id !== 'string') return null;
		return model;
	} catch {
		return null;
	}
}

export type BacktestLaunchInput = {
	/** The server snapshots this strategy's current definition. */
	strategy_id: string;
	dataset_fingerprint: string;
	htf_dataset_fingerprint?: string;
	indicator_dataset_fingerprints?: { timeframe: string; dataset_fingerprint: string }[];
	evaluation_start: string;
	evaluation_end: string;
	initial_quote_balance: string;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	engine_contract_version:
		'thytrader-bar-backtest-v1' | 'thytrader-bar-backtest-v2' | 'thytrader-bar-backtest-v3';
	spread_bps: string | null;
};

export async function submitBacktest(input: BacktestLaunchInput): Promise<BacktestSubmission> {
	return request<BacktestSubmission>('/api/v1/backtests', {
		method: 'POST',
		body: JSON.stringify(input)
	});
}

/** Plain list of what deleting one strategy removes, skipping zero counts. */
export function deletionCountsText(counts: StrategyDeletionCounts): string[] {
	const parts: [number, string, string][] = [
		[counts.backtests, 'backtest', 'backtests'],
		[counts.studies, 'study', 'studies'],
		[counts.research_jobs, 'research job', 'research jobs'],
		[counts.paper_deployments, 'paper bot (with its ledger)', 'paper bots (with their ledgers)'],
		[counts.snapshots, 'rules snapshot', 'rules snapshots'],
		[counts.allocations_removed, 'risk-policy allocation', 'risk-policy allocations']
	];
	const lines = parts
		.filter(([count]) => count > 0)
		.map(([count, one, many]) => `${count} ${count === 1 ? one : many}`);
	if (lines.length === 0) lines.push('No backtests, studies, or bots');
	if (counts.live_deployments_kept > 0) {
		const n = counts.live_deployments_kept;
		lines.push(`${n} stopped live bot${n === 1 ? '' : 's'} kept with full history`);
	}
	return lines;
}

/** One-line outcome for a bulk-delete result row. */
export function bulkOutcomeText(item: BulkDeleteItem): string {
	switch (item.outcome) {
		case 'deleted':
			return 'Deleted';
		case 'would_delete':
			return 'Will be deleted';
		case 'blocked':
			return item.code === 'strategy_has_active_deployments'
				? `Blocked: ${item.deployment_ids.length || 'a'} running or paused bot${item.deployment_ids.length === 1 ? '' : 's'}. Stop ${item.deployment_ids.length === 1 ? 'it' : 'them'} first.`
				: `Blocked: ${item.message ?? 'the server refused this deletion.'}`;
		case 'not_found':
			return 'Not found (already deleted?)';
		case 'failed':
			return `Failed: ${item.message ?? 'no details returned'}`;
	}
}
