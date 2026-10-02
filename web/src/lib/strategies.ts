import templatesDocument from '$lib/generated/strategy-templates.json';
import {
	INDICATOR_CATALOG,
	catalogEntry,
	defaultParameters,
	findCatalogEntry,
	indicatorDisplayLabel,
	isValidOffset,
	isValidParameterValue,
	operandDisplayLabel,
	parameterProblems,
	readParameter,
	writeParameter,
	type IndicatorCatalogEntry,
	type IndicatorField,
	type IndicatorKindValue,
	type IndicatorParameters
} from '$lib/indicator-catalog';
import {
	isAcceptedResearchJob,
	researchJobFailureMessage,
	researchJobFailureStatus,
	waitForResearchJob,
	type ResearchJobAccepted
} from '$lib/research-jobs';
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

/** Advisory finding on a valid document; never blocks a save or a run (ADR 0090). */
export type StrategyValidationWarning = { code: string; loc: string; message: string };

export type StrategyValidation = {
	valid: boolean;
	issues: StrategyValidationIssue[];
	warnings?: StrategyValidationWarning[];
};

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
	/** The document's `metadata.tags` (ADR 0094); filter the library with one. */
	tags?: string[];
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
	/** Portfolio sleeves removed (each journaled in its portfolio; ADR 0088). */
	portfolio_sleeves?: number;
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

/** One OHLCV field: an identity source or a configurable rolling input. */
export type IdentityInput = IndicatorField;

export type { IndicatorKindValue, IndicatorParameters } from './indicator-catalog';

export type IndicatorInput =
	| IdentityInput
	| ['high', 'low', 'close']
	| ['high', 'low', 'close', 'volume']
	| ['high', 'low']
	| ['close', 'volume'];

/** Kind picker options in catalog order (generated from the Python registry). */
export const INDICATOR_KIND_OPTIONS: readonly { kind: IndicatorKindValue; label: string }[] =
	INDICATOR_CATALOG.map((entry) => ({ kind: entry.kind, label: entry.label }));

/** Declared series of every multi-output kind; single-output kinds are absent. */
export const INDICATOR_OUTPUT_SERIES: Readonly<
	Partial<Record<IndicatorKindValue, readonly string[]>>
> = Object.fromEntries(
	INDICATOR_CATALOG.filter((entry) => entry.outputs.length > 0).map((entry) => [
		entry.kind,
		entry.outputs
	])
);

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
	/** Bar lag on the indicator's own clock (1 = previous completed bar); omit for now. */
	offset?: number;
	/**
	 * Reference instrument id this indicator reads (ADR 0096); empty or absent reads the
	 * traded instrument. A sourced indicator uses the reference's timeframe.
	 */
	source?: string;
	parameters: IndicatorParameters;
};

/**
 * One read-only reference series (ADR 0096): indicators may read it with `source`, it is
 * never traded, and a reference bar is used only after it closes.
 */
export type ReferenceInstrumentDraft = { id: string; product_id: string; timeframe: string };

/** Most reference instruments one strategy may declare. */
export const MAX_REFERENCE_INSTRUMENTS = 3;

/** One New-strategy template from the research template catalog. */
export type StrategyTemplateOption = { id: string; name: string; description: string };

function parseTemplateOptions(raw: unknown): StrategyTemplateOption[] {
	if (raw === null || typeof raw !== 'object' || !('templates' in raw)) {
		throw new Error('Generated strategy template catalog is invalid.');
	}
	const templates = raw.templates;
	if (!Array.isArray(templates)) throw new Error('Generated strategy templates must be a list.');
	return templates.map((item: unknown) => {
		if (item === null || typeof item !== 'object') {
			throw new Error('Generated strategy template must be an object.');
		}
		const { id, name, description } = item as Record<string, unknown>;
		if (typeof id !== 'string' || typeof name !== 'string' || typeof description !== 'string') {
			throw new Error('Generated strategy template needs id, name, and description.');
		}
		return { id, name, description };
	});
}

/** Research templates the library's New-strategy picker offers (generated from Python). */
export const STRATEGY_TEMPLATE_OPTIONS: readonly StrategyTemplateOption[] =
	parseTemplateOptions(templatesDocument);

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
	/** Read-only reference series (`data_requirements.reference_instruments`, ADR 0096). */
	reference_instruments: ReferenceInstrumentDraft[];
	indicators: IndicatorDraft[];
	htf_filter: HtfFilterDraft | null;
	entry: { when: ConditionDraft };
	side: 'long' | 'short';
	sizing: { risk_fraction: string; min_quote_notional: string; max_quote_notional: string };
	portfolio_limits: { max_strategy_exposure_fraction: string; max_concurrent_positions: number };
	exits: {
		initial_stop: { kind: string; atr_indicator: string; multiple: string };
		take_profit: TakeProfitDraft;
		trailing_stop:
			| { enabled: false }
			| { enabled: true; kind: 'atr_multiple'; atr_indicator: string; multiple: string };
		time_exit: { max_bars_held: number };
		/** Optional exit rule (ADR 0093); omitted from the document when absent. */
		signal_exit?: SignalExitDraft;
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

/**
 * `exits.signal_exit` (ADR 0093): exit an open position when this rule matches on a
 * closed bar after the fill bar. Same grammar and indicator operands as `entry.when`.
 */
export type SignalExitDraft = { when: ConditionDraft };

const MIRRORED_CROSS: Partial<Record<ComparisonOperatorValue, ComparisonOperatorValue>> = {
	crosses_above: 'crosses_below',
	crosses_below: 'crosses_above'
};

/**
 * Starting rule for a new "Exit when" block: the mirror of the entry's first top-level
 * crossover (`fast crosses above slow` becomes `fast crosses below slow`, so a trend is
 * held until it reverses), else one comparison on the first indicator for the author to edit.
 */
export function defaultSignalExit(
	entry: ConditionDraft,
	indicators: IndicatorDraft[]
): SignalExitDraft {
	const children = 'all' in entry ? entry.all : 'any' in entry ? entry.any : [];
	for (const child of children) {
		if (!('operator' in child)) continue;
		const operator = MIRRORED_CROSS[child.operator];
		if (operator !== undefined) {
			return {
				when: { all: [{ left: { ...child.left }, operator, right: { ...child.right } }] }
			};
		}
	}
	return {
		when: {
			all: [
				{
					left: defaultIndicatorOperand(indicators),
					operator: 'less_than',
					right: { literal: '0' }
				}
			]
		}
	};
}

/** Reward/risk take-profit, or `none`: exit on the stop, ATR trail, or time exit (ADR 0090). */
export type TakeProfitDraft = { kind: 'reward_risk'; multiple: string } | { kind: 'none' };

/** The reward/risk multiple, or null when the strategy declares no take-profit. */
export function takeProfitMultiple(takeProfit: TakeProfitDraft): string | null {
	return takeProfit.kind === 'reward_risk' ? takeProfit.multiple : null;
}

/** One phrase for summaries: `take profit at 2× risk` or `no take-profit`. */
export function takeProfitPhrase(takeProfit: TakeProfitDraft): string {
	const multiple = takeProfitMultiple(takeProfit);
	return multiple === null ? 'no take-profit' : `take profit at ${multiple}× risk`;
}

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

function isIdentityInput(value: unknown): value is IdentityInput {
	return typeof value === 'string' && (IDENTITY_INPUTS as readonly string[]).includes(value);
}

const LOCKED_TUPLE_INPUTS: readonly IndicatorInput[] = [
	['high', 'low', 'close'],
	['high', 'low', 'close', 'volume'],
	['high', 'low'],
	['close', 'volume']
];

/** The canonical locked input of one kind (a fresh copy for field tuples). */
function lockedInputFor(entry: IndicatorCatalogEntry): IndicatorInput {
	const locked = entry.default_input;
	if (typeof locked === 'string') return locked;
	const match = LOCKED_TUPLE_INPUTS.find(
		(candidate) =>
			Array.isArray(candidate) &&
			locked !== null &&
			candidate.length === locked.length &&
			candidate.every((field, index) => field === locked[index])
	);
	if (match === undefined) throw new Error(`No locked input for indicator kind ${entry.kind}.`);
	return structuredClone(match);
}

function selectedRollingInput(
	indicator: IndicatorDraft,
	entry: IndicatorCatalogEntry
): IdentityInput {
	if (isIdentityInput(indicator.input)) return indicator.input;
	return isIdentityInput(entry.default_input) ? entry.default_input : 'close';
}

/** True for rolling kinds that read one author-selected OHLCV field (identity excluded). */
export function isConfigurableRollingKind(kind: IndicatorKindValue): boolean {
	return kind !== 'identity' && findCatalogEntry(kind)?.input_mode === 'configurable';
}

/** Quote currency for labels: `USDC` for `BTC-USDC`, or `quote` while the id is incomplete. */
export function quoteLabelFor(productId: string): string {
	return quoteCurrencyFor(productId.trim().toUpperCase(), 'quote');
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

/**
 * One operand select option. `group` is set for multi-series kinds (the indicator's
 * readable label and id) so the form can render their series as an `<optgroup>`.
 */
export type OperandChoice = { key: string; label: string; group?: string };

/**
 * Expand multi-series kinds into one selectable operand per output, labelled
 * readably (`Supertrend(10, 3) · direction`). Keys stay `indicator:<id>[.<series>]`.
 * Labels that would repeat get ` — <id>` appended so every option stays distinct.
 */
export function operandChoices(
	indicators: IndicatorDraft[],
	references: ReferenceInstrumentDraft[] = []
): OperandChoice[] {
	const choices: (OperandChoice & { id: string })[] = [];
	for (const indicator of indicators) {
		const series = INDICATOR_OUTPUT_SERIES[indicator.kind];
		if (series === undefined) {
			choices.push({
				id: indicator.id,
				key: `indicator:${indicator.id}`,
				label: referenceOperandLabel(indicator, references)
			});
			continue;
		}
		const group = `${referenceOperandLabel(indicator, references, null)} — ${indicator.id}`;
		for (const name of series) {
			choices.push({
				id: indicator.id,
				key: `indicator:${indicator.id}.${name}`,
				label: referenceOperandLabel(indicator, references, name),
				group
			});
		}
	}
	const counts = new Map<string, number>();
	for (const choice of choices) counts.set(choice.label, (counts.get(choice.label) ?? 0) + 1);
	const labelled: OperandChoice[] = choices.map(({ id, key, label, group }) => ({
		key,
		label: (counts.get(label) ?? 0) > 1 ? `${label} — ${id}` : label,
		...(group === undefined ? {} : { group })
	}));
	labelled.push({ key: 'literal', label: 'literal value' });
	return labelled;
}

/** The declared reference an indicator reads, or undefined for the traded instrument. */
export function indicatorReference(
	indicator: { source?: string },
	references: ReferenceInstrumentDraft[]
): ReferenceInstrumentDraft | undefined {
	if (indicator.source === undefined || indicator.source === '') return undefined;
	return references.find((reference) => reference.id === indicator.source);
}

/** Base currency label of one reference (`BTC` for `BTC-USDC`). */
export function referenceBaseLabel(reference: ReferenceInstrumentDraft): string {
	return reference.product_id.split('-')[0] || reference.id;
}

/**
 * Operand label naming the instrument for reference indicators: `BTC · EMA(100) @ 1d`.
 * `series` null renders the indicator itself (multi-series group headers).
 */
export function referenceOperandLabel(
	indicator: IndicatorDraft,
	references: ReferenceInstrumentDraft[],
	series: string | null = null
): string {
	const reference = indicatorReference(indicator, references);
	const display =
		reference === undefined ? indicator : { ...indicator, timeframe: reference.timeframe };
	const label =
		series === null && INDICATOR_OUTPUT_SERIES[indicator.kind] !== undefined
			? indicatorDisplayLabel(display)
			: operandDisplayLabel(display, series ?? undefined);
	return reference === undefined ? label : `${referenceBaseLabel(reference)} · ${label}`;
}

/** Reference clocks for one decision clock: the decision clock or a coarser integer multiple. */
export function validReferenceTimeframes(decisionTimeframe: string): string[] {
	if (TIMEFRAME_SECONDS[decisionTimeframe as ExecutionTimeframe] === undefined) return [];
	return [decisionTimeframe, ...validHtfTimeframes(decisionTimeframe)];
}

/** A new reference: BTC in the strategy's quote on 1d (or the coarsest legal clock). */
export function defaultReferenceInstrument(model: BuilderModel): ReferenceInstrumentDraft {
	const taken = new Set(model.reference_instruments.map((reference) => reference.id));
	let id = 'btc';
	for (let index = 2; taken.has(id); index += 1) id = `ref${index}`;
	const clocks = validReferenceTimeframes(model.timeframe);
	const timeframe = clocks.includes('1d') ? '1d' : (clocks.at(-1) ?? model.timeframe);
	return { id, product_id: `BTC-${quoteCurrencyFor(model.product_id, 'USDC')}`, timeframe };
}

/**
 * Align one builder indicator with its kind: the input the kind accepts, every
 * declared parameter (a same-named value carries over only when it is valid for the
 * new kind and the kind's constraints still hold; otherwise the catalog default),
 * and no timeframe or offset on kinds that cannot take them.
 */
export function applyIndicatorKindDefaults(indicator: IndicatorDraft): void {
	const entry = catalogEntry(indicator.kind);
	if (entry.input_mode === 'none') {
		delete indicator.input;
	} else if (entry.input_mode === 'configurable') {
		indicator.input = selectedRollingInput(indicator, entry);
	} else {
		indicator.input = lockedInputFor(entry);
	}
	if (!entry.supports_timeframe) delete indicator.timeframe;
	if (!entry.supports_offset) delete indicator.offset;
	if (!entry.supports_source) delete indicator.source;
	const carried: IndicatorParameters = {};
	for (const spec of entry.parameters) {
		const previous = readParameter(indicator.parameters, spec.name);
		if (
			(typeof previous === 'number' || typeof previous === 'string') &&
			isValidParameterValue(spec, previous)
		) {
			writeParameter(carried, spec.name, previous);
		} else if (spec.default !== null) {
			writeParameter(carried, spec.name, spec.default);
		}
	}
	indicator.parameters =
		parameterProblems(indicator.kind, carried, '').length === 0
			? carried
			: defaultParameters(indicator.kind);
}

function serializedInput(
	indicator: IndicatorDraft,
	entry: IndicatorCatalogEntry
): IndicatorInput | undefined {
	if (entry.input_mode === 'none') return undefined;
	if (entry.input_mode === 'configurable') return selectedRollingInput(indicator, entry);
	return indicator.input ?? lockedInputFor(entry);
}

function serializedParameters(
	indicator: IndicatorDraft,
	entry: IndicatorCatalogEntry
): IndicatorParameters {
	const parameters: IndicatorParameters = {};
	for (const spec of entry.parameters) {
		const value = readParameter(indicator.parameters, spec.name);
		if (typeof value === 'number' || (typeof value === 'string' && value !== '')) {
			writeParameter(parameters, spec.name, value);
		} else if (!spec.optional && spec.default !== null) {
			writeParameter(parameters, spec.name, spec.default);
		}
	}
	return parameters;
}

/**
 * Canonical indicator payload for saving a strategy. Optional parameters are
 * omitted when blank, `timeframe` only appears for an extra clock, `offset`
 * only appears when it is a positive bar lag on a kind that accepts one, and
 * `source` only appears for a reference-instrument indicator (which then omits
 * `timeframe`: it reads the reference's clock).
 */
export function serializeIndicator(
	indicator: IndicatorDraft,
	decisionTimeframe?: string
): IndicatorDraft {
	const entry = findCatalogEntry(indicator.kind);
	if (entry === undefined) return indicator;
	const source =
		entry.supports_source && indicator.source !== undefined && indicator.source !== ''
			? indicator.source
			: undefined;
	const extraTimeframe =
		source === undefined &&
		decisionTimeframe !== undefined &&
		entry.supports_timeframe &&
		indicator.timeframe !== undefined &&
		indicator.timeframe !== '' &&
		indicator.timeframe !== decisionTimeframe
			? indicator.timeframe
			: undefined;
	const offset =
		entry.supports_offset && isValidOffset(indicator.offset) && indicator.offset > 0
			? indicator.offset
			: undefined;
	const input = serializedInput(indicator, entry);
	return {
		id: indicator.id,
		kind: indicator.kind,
		...(input === undefined ? {} : { input }),
		parameters: serializedParameters(indicator, entry),
		...(extraTimeframe === undefined ? {} : { timeframe: extraTimeframe }),
		...(offset === undefined ? {} : { offset }),
		...(source === undefined ? {} : { source })
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
 * one candle after it, whose open liquidates any inventory still held at the end.
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
	cursor?: string,
	tag?: string | null
): Promise<{ entries: StrategyLibraryEntry[]; nextCursor: string | null; total: number | null }> {
	const params = new URLSearchParams({ limit: String(limit) });
	if (cursor !== undefined) params.set('cursor', cursor);
	if (tag) params.set('tag', tag);
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
		reference_instruments: (
			(strategy.data_requirements as { reference_instruments?: ReferenceInstrumentDraft[] })
				.reference_instruments ?? []
		).map((reference) => ({ ...reference })),
		indicators: ((strategy.indicators as IndicatorDraft[]) ?? []).map((indicator) => ({
			...indicator,
			timeframe: indicator.timeframe ?? '',
			source: indicator.source ?? ''
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
			required_fields: ['open', 'high', 'low', 'close', 'volume'],
			...(model.reference_instruments.length === 0
				? {}
				: {
						reference_instruments: model.reference_instruments.map((reference) => ({
							id: reference.id,
							product_id: reference.product_id.trim().toUpperCase(),
							timeframe: reference.timeframe
						}))
					})
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
	/** Optional constant total bid-ask spread stress (bps); omit for none. */
	spread_bps?: string;
};

/**
 * Run one backtest in the research worker. A run longer than the API's synchronous
 * wait comes back as HTTP 202 with the job; poll it to the same submission shape.
 */
export async function submitBacktest(input: BacktestLaunchInput): Promise<BacktestSubmission> {
	const body = await request<BacktestSubmission | ResearchJobAccepted>('/api/v1/backtests', {
		method: 'POST',
		body: JSON.stringify(input)
	});
	if (!isAcceptedResearchJob(body)) return body;
	const job = await waitForResearchJob(body.job_id);
	if (job.status === 'completed' && job.run_fingerprint && job.result_fingerprint) {
		return {
			run_fingerprint: job.run_fingerprint,
			result_fingerprint: job.result_fingerprint,
			strategy_id: body.strategy_id ?? undefined,
			strategy_fingerprint: body.strategy_fingerprint ?? undefined
		};
	}
	const status = researchJobFailureStatus(job);
	throw new StrategyApiError(
		status,
		job.error_code ?? null,
		`The research operation failed (HTTP ${status}): ${researchJobFailureMessage(job)}`,
		{}
	);
}

/** Plain list of what deleting one strategy removes, skipping zero counts. */
export function deletionCountsText(counts: StrategyDeletionCounts): string[] {
	const parts: [number, string, string][] = [
		[counts.backtests, 'backtest', 'backtests'],
		[counts.studies, 'study', 'studies'],
		[counts.research_jobs, 'research job', 'research jobs'],
		[counts.paper_deployments, 'paper bot (with its ledger)', 'paper bots (with their ledgers)'],
		[counts.snapshots, 'rules snapshot', 'rules snapshots'],
		[counts.allocations_removed, 'risk-policy allocation', 'risk-policy allocations'],
		[
			counts.portfolio_sleeves ?? 0,
			'portfolio sleeve (journaled in its portfolio)',
			'portfolio sleeves (journaled in their portfolios)'
		]
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
