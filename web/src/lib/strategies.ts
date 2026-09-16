export type StrategyDraft = {
	strategy_id: string;
	version: number;
	name: string;
	description: string | null;
	status: 'draft' | 'published' | 'archived';
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

export type StrategyLibraryBacktest = {
	result_fingerprint: string;
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

/** Newest paper then live status tokens shown in the library column. */
export const PAPER_LIVE_STATUS_LEGEND = 'unavailable · running · paused · stopped';

/**
 * Explain the library paper/live column: two tokens, newest deployment per mode.
 * `unavailable` means no runtime of that mode, not an unknown health signal.
 */
export const PAPER_LIVE_STATUS_TITLE =
	'Paper then live. Newest deployment per mode. unavailable = no runtime; running = active; paused = halted (protective exits continue); stopped = ended.';

/**
 * Visible library cell text: paper status, then live status.
 */
export function paperLiveStatusLabel(paperLive: StrategyLibraryPaperLive): string {
	return `${paperLive.paper} / ${paperLive.live}`;
}

/**
 * Tooltip for one library paper/live cell. Names both modes so the slash is not opaque.
 */
export function paperLiveStatusTitle(paperLive: StrategyLibraryPaperLive): string {
	return `Paper: ${paperLive.paper}. Live: ${paperLive.live}. Opens Deploy.`;
}

/**
 * Confirm copy for archiving the latest published fingerprint from the hover toolbar.
 * Names version and fingerprint identity; archive is an append-only hide, not a delete.
 */
export function archiveConfirmMessage(input: {
	name: string;
	latest_version: number | null;
	latest_fingerprint: string;
}): string {
	const version = input.latest_version === null ? 'unknown version' : `v${input.latest_version}`;
	return [
		`Archive ${input.name}?`,
		'',
		`Version: ${version}`,
		`Fingerprint: ${input.latest_fingerprint}`,
		'',
		'This hides the latest published fingerprint from active selection. Canonical evidence stays immutable; older published versions are unchanged.'
	].join('\n');
}

export type StrategyPublishedVersion = {
	version: number;
	strategy_fingerprint: string;
};

export type StrategyLibraryEntry = {
	strategy_id: string;
	name: string;
	product_id: string;
	timeframe: string;
	latest_version: number | null;
	status: string;
	latest_fingerprint: string | null;
	published_versions: StrategyPublishedVersion[];
	archived: boolean;
	summary: string;
	backtest: StrategyLibraryBacktest | null;
	paper_live: StrategyLibraryPaperLive;
	created_at: string;
	updated_at: string;
};

export type StrategyCreatedResponse = {
	strategy: StrategyDraft;
	revision: number;
	created: StrategyLibraryEntry;
	siblings: StrategyLibraryEntry[];
};

export type DraftVersionResponse = { strategy: StrategyDraft; revision: number };

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
	| 'roc'
	| 'williams_r'
	| 'cci'
	| 'wma'
	| 'momentum'
	| 'mfi'
	| 'macd'
	| 'bollinger'
	| 'identity'
	| 'constant';

export const INDICATOR_KIND_OPTIONS: readonly { kind: IndicatorKindValue; label: string }[] = [
	{ kind: 'ema', label: 'EMA' },
	{ kind: 'sma', label: 'SMA' },
	{ kind: 'rsi', label: 'RSI' },
	{ kind: 'atr', label: 'ATR' },
	{ kind: 'volume_sma', label: 'Volume SMA' },
	{ kind: 'highest', label: 'Highest high' },
	{ kind: 'lowest', label: 'Lowest low' },
	{ kind: 'stdev', label: 'Stdev' },
	{ kind: 'roc', label: 'ROC' },
	{ kind: 'williams_r', label: 'Williams %R' },
	{ kind: 'cci', label: 'CCI' },
	{ kind: 'wma', label: 'WMA' },
	{ kind: 'momentum', label: 'Momentum' },
	{ kind: 'mfi', label: 'MFI' },
	{ kind: 'macd', label: 'MACD' },
	{ kind: 'bollinger', label: 'Bollinger' },
	{ kind: 'identity', label: 'OHLCV' },
	{ kind: 'constant', label: 'Constant' }
];

export const INDICATOR_OUTPUT_SERIES: Readonly<
	Partial<Record<IndicatorKindValue, readonly string[]>>
> = {
	macd: ['macd', 'signal', 'histogram'],
	bollinger: ['middle', 'upper', 'lower']
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

export type BuilderModel = {
	strategy_id: string;
	version: number;
	revision: number;
	name: string;
	description: string;
	status: string;
	created_at: string;
	product_id: string;
	base_currency: string;
	timeframe: string;
	warmup_bars: number;
	indicators: IndicatorDraft[];
	htf_filter: HtfFilterDraft | null;
	entry: { when: ConditionDraft };
	sizing: { risk_fraction: string; min_quote_notional: string; max_quote_notional: string };
	portfolio_limits: { max_strategy_exposure_fraction: string };
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

function lockedIndicatorInput(kind: IndicatorKindValue): IndicatorInput {
	if (kind === 'atr' || kind === 'williams_r' || kind === 'cci') {
		return ['high', 'low', 'close'];
	}
	if (kind === 'mfi') return ['high', 'low', 'close', 'volume'];
	if (kind === 'volume_sma') return 'volume';
	if (kind === 'highest') return 'high';
	if (kind === 'lowest') return 'low';
	return 'close';
}

function indicatorPeriodMax(kind: IndicatorKindValue): number {
	return kind === 'rsi' ||
		kind === 'atr' ||
		kind === 'williams_r' ||
		kind === 'cci' ||
		kind === 'mfi'
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
	indicator.input = lockedIndicatorInput(indicator.kind);
	const previousPeriod = indicator.parameters.period;
	const maximum = indicatorPeriodMax(indicator.kind);
	const period =
		typeof previousPeriod === 'number' && Number.isInteger(previousPeriod) && previousPeriod >= 2
			? Math.min(previousPeriod, maximum)
			: 50;
	indicator.parameters = { period };
}

/** Canonical indicator payload for draft save/publish. */
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

export type DraftResponse = { strategy: StrategyDraft; revision: number; summary: string };
type StrategyLibraryResponse = { strategies: StrategyLibraryEntry[] };
type PublishedStrategy = { strategy_fingerprint: string; strategy: StrategyDraft };
type ArchivedStrategy = { strategy_fingerprint: string; archived_at: string | null };
type BacktestSubmission = { run_fingerprint: string; result_fingerprint: string };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await fetch(url, {
		...init,
		headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...init?.headers }
	});
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as {
			detail?: string | { message?: string };
		};
		const detail =
			typeof body.detail === 'string'
				? body.detail
				: (body.detail?.message ?? 'no details returned');
		throw new Error(`The research operation failed (HTTP ${response.status}): ${detail}`);
	}
	return (await response.json()) as T;
}

export async function createDraft(options?: {
	template?: string;
	product_id?: string;
	timeframe?: string;
}): Promise<StrategyCreatedResponse> {
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
	return request<StrategyCreatedResponse>(path, { method: 'POST' });
}

export async function listStrategies(): Promise<StrategyLibraryEntry[]> {
	return (await request<StrategyLibraryResponse>('/api/v1/strategies')).strategies;
}

export async function clonePublishedStrategy(fingerprint: string): Promise<DraftResponse> {
	return request<DraftResponse>('/api/v1/strategies/clone', {
		method: 'POST',
		body: JSON.stringify({ strategy_fingerprint: fingerprint })
	});
}

export async function importStrategy(definition: unknown): Promise<DraftResponse> {
	return request<DraftResponse>('/api/v1/strategies/import', {
		method: 'POST',
		body: JSON.stringify({ strategy: definition })
	});
}

export async function fetchStrategySource(fingerprint: string): Promise<StrategyDraft> {
	return (
		await request<{ strategy: StrategyDraft }>(
			`/api/v1/strategies/source/${encodeURIComponent(fingerprint)}`
		)
	).strategy;
}

export async function fetchDraftVersion(
	strategyId: string,
	version: number
): Promise<DraftVersionResponse> {
	return request<DraftVersionResponse>(
		`/api/v1/strategies/${encodeURIComponent(strategyId)}/versions/${version}`
	);
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

export function toBuilderModel(strategy: StrategyDraft, revision: number): BuilderModel {
	const entry = strategy.entry as { when: ConditionDraft; cooldown_bars: number };
	const exits = strategy.exits as BuilderModel['exits'];
	return {
		strategy_id: strategy.strategy_id,
		version: strategy.version,
		revision,
		name: strategy.name,
		description: strategy.description ?? '',
		status: strategy.status,
		created_at: strategy.created_at,
		product_id: (strategy.instrument as { product_id: string }).product_id,
		base_currency: (strategy.instrument as { base_currency: string }).base_currency,
		timeframe: strategy.timeframe as string,
		warmup_bars: (strategy.data_requirements as { warmup_bars: number }).warmup_bars,
		indicators: ((strategy.indicators as IndicatorDraft[]) ?? []).map((indicator) => ({
			...indicator,
			timeframe: indicator.timeframe ?? ''
		})),
		htf_filter: toHtfFilterDraft(strategy.htf_filter),
		entry: { when: entry.when },
		sizing: {
			risk_fraction: strategy.sizing.risk_fraction,
			min_quote_notional: strategy.sizing.min_quote_notional,
			max_quote_notional: strategy.sizing.max_quote_notional
		},
		portfolio_limits: {
			max_strategy_exposure_fraction: strategy.portfolio_limits.max_strategy_exposure_fraction
		},
		exits,
		execution: strategy.execution as BuilderModel['execution'],
		cooldown_bars: entry.cooldown_bars,
		metadata: strategy.metadata as BuilderModel['metadata']
	};
}

export function fromBuilderModel(model: BuilderModel): StrategyDraft {
	return {
		schema_version: '1.0',
		strategy_id: model.strategy_id,
		version: model.version,
		name: model.name,
		description: model.description.trim().length > 0 ? model.description : null,
		status: 'draft',
		created_at: model.created_at,
		instrument: {
			product_id: model.product_id,
			base_currency: model.base_currency,
			quote_currency: 'USD'
		},
		timeframe: model.timeframe,
		data_requirements: {
			warmup_bars: model.warmup_bars,
			required_fields: ['open', 'high', 'low', 'close', 'volume']
		},
		indicators: model.indicators.map((indicator) =>
			serializeIndicator(indicator, model.timeframe)
		),
		...(model.htf_filter === null
			? {}
			: {
					htf_filter: {
						timeframe: model.htf_filter.timeframe,
						data_requirements: {
							warmup_bars: model.htf_filter.warmup_bars,
							required_fields: ['open', 'high', 'low', 'close', 'volume']
						},
						indicators: model.htf_filter.indicators.map(serializeIndicator),
						when: model.htf_filter.when
					}
				}),
		entry: {
			side: 'long',
			when: model.entry.when,
			cooldown_bars: model.cooldown_bars,
			max_open_positions: 1
		},
		sizing: { kind: 'risk_fraction', ...model.sizing },
		portfolio_limits: { ...model.portfolio_limits, max_concurrent_positions: 1 },
		exits: model.exits,
		execution: model.execution,
		metadata: model.metadata
	};
}

export async function saveDraft(draft: StrategyDraft, revision: number): Promise<DraftResponse> {
	return request<DraftResponse>(
		`/api/v1/strategies/${encodeURIComponent(draft.strategy_id)}/versions/${draft.version}`,
		{ method: 'PUT', body: JSON.stringify({ strategy: draft, revision }) }
	);
}

export async function listDatasets(): Promise<Dataset[]> {
	return (await request<{ datasets: Dataset[] }>('/api/v1/market-data/datasets/latest')).datasets;
}

export async function publishDraft(
	draft: StrategyDraft,
	revision: number
): Promise<PublishedStrategy> {
	return request<PublishedStrategy>(
		`/api/v1/strategies/${encodeURIComponent(draft.strategy_id)}/publish`,
		{
			method: 'POST',
			body: JSON.stringify({ strategy: draft, revision })
		}
	);
}

export async function archivePublishedStrategy(fingerprint: string): Promise<ArchivedStrategy> {
	return request<ArchivedStrategy>(
		`/api/v1/strategies/${encodeURIComponent(fingerprint)}/archive`,
		{ method: 'POST' }
	);
}

export type StrategyVersionHistoryEntry = {
	version: number;
	strategy_fingerprint: string;
	published: boolean;
	archived: boolean;
	archived_at: string | null;
	backtest: StrategyLibraryBacktest | null;
};

export type StrategyVersionHistory = {
	strategy_id: string;
	latest_version: number | null;
	next_version: number;
	versions: StrategyVersionHistoryEntry[];
	draft: DraftResponse | null;
};

export async function fetchStrategyHistory(strategyId: string): Promise<StrategyVersionHistory> {
	return request<StrategyVersionHistory>(
		`/api/v1/strategies/${encodeURIComponent(strategyId)}/history`
	);
}

export type RevisionResponse = {
	strategy: StrategyDraft;
	revision: number;
	source_fingerprint: string;
	summary: string;
};

export async function reviseStrategy(
	strategyId: string,
	fingerprint: string
): Promise<RevisionResponse> {
	return request<RevisionResponse>(`/api/v1/strategies/${encodeURIComponent(strategyId)}/revise`, {
		method: 'POST',
		body: JSON.stringify({ strategy_fingerprint: fingerprint })
	});
}

export type BacktestLaunchInput = {
	strategy_fingerprint: string;
	dataset_fingerprint: string;
	htf_dataset_fingerprint?: string;
	indicator_dataset_fingerprints?: { timeframe: string; dataset_fingerprint: string }[];
	evaluation_start: string;
	evaluation_end: string;
	initial_quote_balance: string;
	maker_fee_rate: string;
	taker_fee_rate: string;
	fixed_slippage_bps: string;
	engine_contract_version: 'thytrader-bar-backtest-v1' | 'thytrader-bar-backtest-v2';
	spread_bps: string | null;
};

export async function submitBacktest(input: BacktestLaunchInput): Promise<BacktestSubmission> {
	return request<BacktestSubmission>('/api/v1/backtests', {
		method: 'POST',
		body: JSON.stringify(input)
	});
}
