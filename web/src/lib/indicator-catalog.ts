/**
 * Typed indicator catalog for the strategy builder.
 *
 * The catalog data (kinds, labels, categories, one-line help, input policy,
 * parameter bounds and defaults, output series, warmup formulas) is generated
 * from the Python registry into `generated/indicator-catalog.json` by
 * `scripts/export_indicator_catalog.py`; the backend test suite fails when the
 * checked-in file drifts. This module validates that file once at load, adds the
 * warmup formulas the builder evaluates locally (cross-checked against the
 * backend in `indicator-catalog.spec.ts`), parameter validation, search, and
 * readable labels such as `Supertrend(10, 3) · direction`.
 */
import catalogDocument from './generated/indicator-catalog.json';

/** Every implemented kind, in catalog order (the historical 21 first). */
export const INDICATOR_KINDS = [
	'ema',
	'sma',
	'rsi',
	'atr',
	'volume_sma',
	'highest',
	'lowest',
	'stdev',
	'stdev_sample',
	'roc',
	'williams_r',
	'cci',
	'wma',
	'momentum',
	'mfi',
	'macd',
	'bollinger',
	'stochastic',
	'adx',
	'identity',
	'constant',
	'dema',
	'tema',
	'hma',
	'kama',
	'vwma',
	'supertrend',
	'parabolic_sar',
	'aroon',
	'ichimoku',
	'vortex',
	'linear_regression',
	'trix',
	'stochastic_rsi',
	'ppo',
	'ultimate_oscillator',
	'awesome_oscillator',
	'cmo',
	'tsi',
	'keltner',
	'donchian',
	'bollinger_percent_b',
	'bollinger_bandwidth',
	'natr',
	'choppiness',
	'historical_volatility',
	'obv',
	'cmf',
	'accumulation_distribution',
	'vwap',
	'force_index',
	'zscore',
	'percent_rank'
] as const;

export type IndicatorKindValue = (typeof INDICATOR_KINDS)[number];

/** Every parameter name any kind declares. */
export const INDICATOR_PARAMETER_NAMES = [
	'period',
	'value',
	'fast_period',
	'slow_period',
	'signal_period',
	'stdev_multiplier',
	'k_period',
	'd_period',
	'multiplier',
	'step',
	'max_step',
	'tenkan_period',
	'kijun_period',
	'senkou_b_period',
	'rsi_period',
	'stoch_period',
	'short_period',
	'medium_period',
	'long_period',
	'atr_period',
	'annualization_periods'
] as const;

export type IndicatorParameterName = (typeof INDICATOR_PARAMETER_NAMES)[number];

/**
 * Parameter object of one indicator draft. Integer parameters hold numbers and
 * decimal parameters hold exact decimal strings, as in the canonical document.
 */
export type IndicatorParameters = {
	period?: number;
	value?: string;
	fast_period?: number;
	slow_period?: number;
	signal_period?: number;
	stdev_multiplier?: string;
	k_period?: number;
	d_period?: number;
	multiplier?: string;
	step?: string;
	max_step?: string;
	tenkan_period?: number;
	kijun_period?: number;
	senkou_b_period?: number;
	rsi_period?: number;
	stoch_period?: number;
	short_period?: number;
	medium_period?: number;
	long_period?: number;
	atr_period?: number;
	annualization_periods?: number;
};

export type IndicatorField = 'open' | 'high' | 'low' | 'close' | 'volume';
export type IndicatorCategory =
	'trend' | 'momentum' | 'volatility' | 'volume' | 'statistical' | 'price';
export type IndicatorInputMode = 'configurable' | 'locked' | 'none';

export type IndicatorParameterSpec = {
	name: IndicatorParameterName;
	label: string;
	value_type: 'integer' | 'decimal';
	/** Integer bound, canonical decimal text, or null when unbounded. */
	minimum: number | string | null;
	maximum: number | string | null;
	/** Decimals that must be strictly greater than `minimum`. */
	exclusive_minimum: boolean;
	/** Builder default; null for optional parameters. */
	default: number | string | null;
	/** Omitted from the document unless the author sets it. */
	optional: boolean;
	help: string;
};

export type IndicatorCatalogEntry = {
	kind: IndicatorKindValue;
	label: string;
	category: IndicatorCategory;
	summary: string;
	inputs: IndicatorField[];
	input_mode: IndicatorInputMode;
	default_input: IndicatorField | IndicatorField[] | null;
	parameter_kind: string;
	parameters: IndicatorParameterSpec[];
	/** Cross-parameter rules such as `fast_period < slow_period`. */
	constraints: string[];
	/** Series names for multi-output kinds; empty for single-output kinds. */
	outputs: string[];
	/** Human warmup formula, for example `slow_period + signal_period - 1`. */
	warmup: string;
	default_warmup_bars: number;
	supports_timeframe: boolean;
	supports_offset: boolean;
	/** Whether the kind may read a reference instrument with `source` (ADR 0096). */
	supports_source: boolean;
};

/** Largest bar lag one declaration may request (`offset`). */
export const MAX_INDICATOR_OFFSET = 500;

/** Picker groups in display order. */
export const INDICATOR_CATEGORIES: readonly { id: IndicatorCategory; label: string }[] = [
	{ id: 'trend', label: 'Trend' },
	{ id: 'momentum', label: 'Momentum' },
	{ id: 'volatility', label: 'Volatility' },
	{ id: 'volume', label: 'Volume' },
	{ id: 'statistical', label: 'Statistical' },
	{ id: 'price', label: 'Price' }
];

const FIELDS: readonly IndicatorField[] = ['open', 'high', 'low', 'close', 'volume'];
const CATEGORY_IDS: readonly IndicatorCategory[] = INDICATOR_CATEGORIES.map(
	(category) => category.id
);
const INPUT_MODES: readonly IndicatorInputMode[] = ['configurable', 'locked', 'none'];
const VALUE_TYPES: readonly IndicatorParameterSpec['value_type'][] = ['integer', 'decimal'];
const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

function fail(where: string, expected: string): never {
	throw new Error(`Generated indicator catalog is invalid at ${where}: expected ${expected}.`);
}

function asRecord(value: unknown, where: string): Record<string, unknown> {
	if (value === null || typeof value !== 'object' || Array.isArray(value)) fail(where, 'object');
	return value as Record<string, unknown>;
}

function asText(value: unknown, where: string): string {
	if (typeof value !== 'string') fail(where, 'string');
	return value;
}

function asBoolean(value: unknown, where: string): boolean {
	if (typeof value !== 'boolean') fail(where, 'boolean');
	return value;
}

function asInteger(value: unknown, where: string): number {
	if (typeof value !== 'number' || !Number.isInteger(value)) fail(where, 'integer');
	return value;
}

function asList(value: unknown, where: string): unknown[] {
	if (!Array.isArray(value)) fail(where, 'array');
	return value;
}

function asTextList(value: unknown, where: string): string[] {
	return asList(value, where).map((item, index) => asText(item, `${where}[${index}]`));
}

function asOneOf<T extends string>(value: unknown, allowed: readonly T[], where: string): T {
	const found = allowed.find((item) => item === value);
	if (found === undefined) fail(where, allowed.join(' | '));
	return found;
}

function asBound(value: unknown, where: string): number | string | null {
	if (value === null || typeof value === 'number' || typeof value === 'string') return value;
	return fail(where, 'number, string, or null');
}

function asFieldList(value: unknown, where: string): IndicatorField[] {
	return asList(value, where).map((item, index) => asOneOf(item, FIELDS, `${where}[${index}]`));
}

function asDefaultInput(value: unknown, where: string): IndicatorField | IndicatorField[] | null {
	if (value === null) return null;
	if (Array.isArray(value)) return asFieldList(value, where);
	return asOneOf(value, FIELDS, where);
}

function parseParameter(raw: unknown, where: string): IndicatorParameterSpec {
	const item = asRecord(raw, where);
	return {
		name: asOneOf(item.name, INDICATOR_PARAMETER_NAMES, `${where}.name`),
		label: asText(item.label, `${where}.label`),
		value_type: asOneOf(item.value_type, VALUE_TYPES, `${where}.value_type`),
		minimum: asBound(item.minimum, `${where}.minimum`),
		maximum: asBound(item.maximum, `${where}.maximum`),
		exclusive_minimum: asBoolean(item.exclusive_minimum, `${where}.exclusive_minimum`),
		default: asBound(item.default, `${where}.default`),
		optional: asBoolean(item.optional, `${where}.optional`),
		help: asText(item.help, `${where}.help`)
	};
}

function parseEntry(raw: unknown, where: string): IndicatorCatalogEntry {
	const item = asRecord(raw, where);
	return {
		kind: asOneOf(item.kind, INDICATOR_KINDS, `${where}.kind`),
		label: asText(item.label, `${where}.label`),
		category: asOneOf(item.category, CATEGORY_IDS, `${where}.category`),
		summary: asText(item.summary, `${where}.summary`),
		inputs: asFieldList(item.inputs, `${where}.inputs`),
		input_mode: asOneOf(item.input_mode, INPUT_MODES, `${where}.input_mode`),
		default_input: asDefaultInput(item.default_input, `${where}.default_input`),
		parameter_kind: asText(item.parameter_kind, `${where}.parameter_kind`),
		parameters: asList(item.parameters, `${where}.parameters`).map((parameter, index) =>
			parseParameter(parameter, `${where}.parameters[${index}]`)
		),
		constraints: asTextList(item.constraints, `${where}.constraints`),
		outputs: asTextList(item.outputs, `${where}.outputs`),
		warmup: asText(item.warmup, `${where}.warmup`),
		default_warmup_bars: asInteger(item.default_warmup_bars, `${where}.default_warmup_bars`),
		supports_timeframe: asBoolean(item.supports_timeframe, `${where}.supports_timeframe`),
		supports_offset: asBoolean(item.supports_offset, `${where}.supports_offset`),
		supports_source: asBoolean(item.supports_source, `${where}.supports_source`)
	};
}

/** Validate the generated catalog document; every kind must appear exactly once. */
export function parseIndicatorCatalog(raw: unknown): IndicatorCatalogEntry[] {
	const document = asRecord(raw, 'document');
	const entries = asList(document.indicators, 'indicators').map((entry, index) =>
		parseEntry(entry, `indicators[${index}]`)
	);
	const kinds = entries.map((entry) => entry.kind);
	if (new Set(kinds).size !== kinds.length || kinds.length !== INDICATOR_KINDS.length) {
		fail('indicators', `each of the ${INDICATOR_KINDS.length} kinds exactly once`);
	}
	return entries;
}

/** Every implemented kind with its generated description, in catalog order. */
export const INDICATOR_CATALOG: readonly IndicatorCatalogEntry[] =
	parseIndicatorCatalog(catalogDocument);

const ENTRIES_BY_KIND = new Map<string, IndicatorCatalogEntry>(
	INDICATOR_CATALOG.map((entry) => [entry.kind, entry])
);

/** Narrow a string to an implemented kind. */
export function isIndicatorKind(value: string): value is IndicatorKindValue {
	return ENTRIES_BY_KIND.has(value);
}

/** The catalog entry for one implemented kind. */
export function catalogEntry(kind: IndicatorKindValue): IndicatorCatalogEntry {
	const entry = ENTRIES_BY_KIND.get(kind);
	if (entry === undefined) throw new Error(`Unknown indicator kind: ${kind}`);
	return entry;
}

/** The catalog entry for a possibly unknown kind string, or undefined. */
export function findCatalogEntry(kind: string): IndicatorCatalogEntry | undefined {
	return ENTRIES_BY_KIND.get(kind);
}

/** Display label of one kind (`Supertrend`, `Bollinger %B`, …). */
export function kindLabel(kind: string): string {
	return ENTRIES_BY_KIND.get(kind)?.label ?? kind;
}

/** Builder defaults for one kind; optional parameters are omitted. */
export function defaultParameters(kind: IndicatorKindValue): IndicatorParameters {
	const parameters: IndicatorParameters = {};
	for (const spec of catalogEntry(kind).parameters) {
		if (spec.default !== null) writeParameter(parameters, spec.name, spec.default);
	}
	return parameters;
}

/** Copy of the kind's default input (a fresh array for locked field tuples). */
export function defaultInputFor(
	entry: IndicatorCatalogEntry
): IndicatorField | IndicatorField[] | undefined {
	const value = entry.default_input;
	if (value === null) return undefined;
	return Array.isArray(value) ? [...value] : value;
}

/** Read one parameter without trusting the draft's static type. */
export function readParameter(parameters: object, name: string): unknown {
	return (parameters as Readonly<Record<string, unknown>>)[name];
}

/** Write one parameter, or remove it when `value` is undefined. */
export function writeParameter(
	parameters: IndicatorParameters,
	name: IndicatorParameterName,
	value: number | string | undefined
): void {
	const writable = parameters as Record<string, number | string | undefined>;
	if (value === undefined) {
		delete writable[name];
		return;
	}
	writable[name] = value;
}

function isBlank(value: unknown): boolean {
	return value === undefined || value === null || value === '';
}

function numberOf(parameters: object, name: string): number {
	const value = readParameter(parameters, name);
	return typeof value === 'number' ? value : Number(value);
}

type WarmupRule = (parameters: object) => number;

const period: WarmupRule = (p) => numberOf(p, 'period');
const periodPlusOne: WarmupRule = (p) => numberOf(p, 'period') + 1;
const one: WarmupRule = () => 1;
const fastSlowSignal: WarmupRule = (p) =>
	numberOf(p, 'slow_period') + numberOf(p, 'signal_period') - 1;
const signalLine: WarmupRule = (p) => numberOf(p, 'signal_period');

/** Closed bars before every output is defined, mirroring the schema's warmup rules. */
const WARMUP_RULES: Record<IndicatorKindValue, WarmupRule> = {
	ema: period,
	sma: period,
	rsi: periodPlusOne,
	atr: period,
	volume_sma: period,
	highest: period,
	lowest: period,
	stdev: period,
	stdev_sample: period,
	roc: periodPlusOne,
	williams_r: period,
	cci: period,
	wma: period,
	momentum: periodPlusOne,
	mfi: periodPlusOne,
	macd: fastSlowSignal,
	bollinger: period,
	stochastic: (p) => numberOf(p, 'k_period') + numberOf(p, 'd_period') - 1,
	adx: (p) => 2 * numberOf(p, 'period') - 1,
	identity: one,
	constant: one,
	dema: (p) => 2 * numberOf(p, 'period') - 1,
	tema: (p) => 3 * numberOf(p, 'period') - 2,
	hma: (p) => {
		const length = numberOf(p, 'period');
		return length + Math.floor(Math.sqrt(length)) - 1;
	},
	kama: periodPlusOne,
	vwma: period,
	supertrend: period,
	parabolic_sar: () => 2,
	aroon: periodPlusOne,
	ichimoku: (p) =>
		Math.max(
			numberOf(p, 'tenkan_period'),
			numberOf(p, 'kijun_period'),
			numberOf(p, 'senkou_b_period')
		),
	vortex: periodPlusOne,
	linear_regression: period,
	trix: (p) => 3 * numberOf(p, 'period') - 1,
	stochastic_rsi: (p) =>
		numberOf(p, 'rsi_period') +
		numberOf(p, 'stoch_period') +
		numberOf(p, 'k_period') +
		numberOf(p, 'd_period') -
		2,
	ppo: fastSlowSignal,
	ultimate_oscillator: (p) => numberOf(p, 'long_period') + 1,
	awesome_oscillator: (p) => numberOf(p, 'slow_period'),
	cmo: periodPlusOne,
	tsi: (p) =>
		numberOf(p, 'long_period') + numberOf(p, 'short_period') + numberOf(p, 'signal_period') - 1,
	keltner: (p) => Math.max(numberOf(p, 'period'), numberOf(p, 'atr_period')),
	donchian: period,
	bollinger_percent_b: period,
	bollinger_bandwidth: period,
	natr: period,
	choppiness: period,
	historical_volatility: periodPlusOne,
	obv: signalLine,
	cmf: period,
	accumulation_distribution: signalLine,
	vwap: period,
	force_index: periodPlusOne,
	zscore: period,
	percent_rank: periodPlusOne
};

/** True for an integer bar lag the schema accepts (0–500). */
export function isValidOffset(value: unknown): value is number {
	return (
		typeof value === 'number' &&
		Number.isInteger(value) &&
		value >= 0 &&
		value <= MAX_INDICATOR_OFFSET
	);
}

/** Declared bar lag, or 0 when omitted or invalid. */
export function offsetOf(indicator: { offset?: unknown }): number {
	return isValidOffset(indicator.offset) ? indicator.offset : 0;
}

/**
 * Closed bars one declaration needs before every output is defined, including its
 * bar lag. Unknown kinds need 0 so they never drive the warmup requirement.
 */
export function indicatorWarmupBars(indicator: {
	kind: string;
	parameters: object;
	offset?: unknown;
}): number {
	if (!isIndicatorKind(indicator.kind)) return 0;
	return WARMUP_RULES[indicator.kind](indicator.parameters) + offsetOf(indicator);
}

function boundNumber(value: number | string | null): number | null {
	return value === null ? null : Number(value);
}

function describeRange(spec: IndicatorParameterSpec): string {
	const minimum = spec.minimum;
	const maximum = spec.maximum;
	if (minimum !== null && maximum !== null) {
		return spec.exclusive_minimum
			? `greater than ${minimum} and at most ${maximum}`
			: `between ${minimum} and ${maximum}`;
	}
	if (minimum !== null) {
		return spec.exclusive_minimum ? `greater than ${minimum}` : `at least ${minimum}`;
	}
	if (maximum !== null) return `at most ${maximum}`;
	return '';
}

function withinBounds(spec: IndicatorParameterSpec, value: number): boolean {
	const minimum = boundNumber(spec.minimum);
	const maximum = boundNumber(spec.maximum);
	if (minimum !== null && (spec.exclusive_minimum ? value <= minimum : value < minimum)) {
		return false;
	}
	return maximum === null || value <= maximum;
}

/** One parameter's problem, or null when the value satisfies its spec. */
export function parameterProblem(
	spec: IndicatorParameterSpec,
	value: unknown,
	subject: string
): string | null {
	const name = `${subject} ${spec.label.toLowerCase()}`;
	if (isBlank(value)) return spec.optional ? null : `${name} is required.`;
	if (spec.value_type === 'integer') {
		const parsed = typeof value === 'number' ? value : Number(value);
		const range = describeRange(spec);
		if (!Number.isInteger(parsed) || !withinBounds(spec, parsed)) {
			return range === '' ? `${name} must be an integer.` : `${name} must be an integer ${range}.`;
		}
		return null;
	}
	if (typeof value !== 'string' || !DECIMAL_PATTERN.test(value)) {
		return `${name} must be a plain decimal number.`;
	}
	if (!withinBounds(spec, Number(value))) return `${name} must be ${describeRange(spec)}.`;
	return null;
}

/** True when `value` satisfies one parameter spec (blank only when optional). */
export function isValidParameterValue(spec: IndicatorParameterSpec, value: unknown): boolean {
	return parameterProblem(spec, value, '') === null;
}

/**
 * Evaluate one catalog constraint chain such as `tenkan_period < kijun_period <
 * senkou_b_period` or `step <= max_step`. Unparseable chains are ignored; the
 * server validates the document either way.
 */
function constraintHolds(constraint: string, parameters: object): boolean {
	const tokens = constraint.trim().split(/\s+/);
	if (tokens.length < 3 || tokens.length % 2 === 0) return true;
	for (let index = 1; index < tokens.length; index += 2) {
		const operator = tokens[index];
		const left = numberOf(parameters, tokens[index - 1]);
		const right = numberOf(parameters, tokens[index + 1]);
		if (!Number.isFinite(left) || !Number.isFinite(right)) return true;
		if (operator === '<' && !(left < right)) return false;
		if (operator === '<=' && !(left <= right)) return false;
	}
	return true;
}

function constraintText(entry: IndicatorCatalogEntry, constraint: string): string {
	const labels = new Map<string, string>(
		entry.parameters.map((spec) => [spec.name, spec.label.toLowerCase()])
	);
	return constraint
		.trim()
		.split(/\s+/)
		.map((token) => labels.get(token) ?? token)
		.join(' ');
}

/**
 * Every parameter problem for one declaration: missing or out-of-range values,
 * non-decimal text, parameters the kind does not take, and broken constraints.
 */
export function parameterProblems(
	kind: IndicatorKindValue,
	parameters: object,
	subject: string
): string[] {
	const entry = catalogEntry(kind);
	const problems: string[] = [];
	const declared = new Set<string>(entry.parameters.map((spec) => spec.name));
	for (const [name, value] of Object.entries(parameters)) {
		if (!declared.has(name) && !isBlank(value)) {
			problems.push(`${subject} ${entry.label} does not take ${name}.`);
		}
	}
	for (const spec of entry.parameters) {
		const problem = parameterProblem(spec, readParameter(parameters, spec.name), subject);
		if (problem !== null) problems.push(problem);
	}
	if (problems.length > 0) return problems;
	for (const constraint of entry.constraints) {
		if (!constraintHolds(constraint, parameters)) {
			problems.push(`${subject} ${entry.label} needs ${constraintText(entry, constraint)}.`);
		}
	}
	return problems;
}

function sameFields(left: unknown, right: readonly IndicatorField[]): boolean {
	return (
		Array.isArray(left) &&
		left.length === right.length &&
		left.every((field, index) => field === right[index])
	);
}

/** True when `input` is what the kind accepts (one source, the locked tuple, or none). */
export function inputMatchesKind(entry: IndicatorCatalogEntry, input: unknown): boolean {
	if (entry.input_mode === 'none') return input === undefined || input === null;
	if (entry.input_mode === 'configurable') {
		return typeof input === 'string' && (entry.inputs as string[]).includes(input);
	}
	const locked = entry.default_input;
	if (locked === null) return false;
	return Array.isArray(locked) ? sameFields(input, locked) : input === locked;
}

/** Search aliases beyond each kind's id, label, category, and summary. */
const KIND_ALIASES: Partial<Record<IndicatorKindValue, readonly string[]>> = {
	ema: ['exponential', 'moving average'],
	sma: ['simple', 'moving average'],
	wma: ['weighted', 'moving average'],
	atr: ['average true range'],
	stdev: ['standard deviation'],
	stdev_sample: ['standard deviation'],
	williams_r: ['%r', 'williams'],
	cci: ['commodity'],
	mfi: ['money flow'],
	bollinger: ['bb', 'bands'],
	adx: ['dmi', 'directional'],
	identity: ['ohlcv', 'price', 'field', 'open', 'high', 'low', 'close', 'volume'],
	constant: ['level', 'threshold'],
	dema: ['double', 'moving average'],
	tema: ['triple', 'moving average'],
	hma: ['hull', 'moving average'],
	kama: ['kaufman', 'adaptive', 'moving average'],
	vwma: ['volume weighted', 'moving average'],
	supertrend: ['super trend'],
	parabolic_sar: ['sar', 'psar', 'stop and reverse'],
	ichimoku: ['cloud', 'kumo', 'tenkan', 'kijun'],
	vortex: ['vi'],
	linear_regression: ['linreg', 'regression', 'slope'],
	stochastic_rsi: ['stochrsi', 'stoch rsi'],
	ppo: ['percentage price'],
	ultimate_oscillator: ['uo'],
	awesome_oscillator: ['ao'],
	cmo: ['chande'],
	tsi: ['true strength'],
	keltner: ['kc', 'squeeze', 'channel'],
	donchian: ['channel', 'breakout', 'turtle'],
	bollinger_percent_b: ['bb', '%b', 'percent b'],
	bollinger_bandwidth: ['bb', 'bbw', 'width', 'squeeze'],
	natr: ['normalized atr'],
	choppiness: ['chop', 'ci'],
	historical_volatility: ['hv', 'realized volatility'],
	obv: ['on balance volume', 'on-balance'],
	cmf: ['chaikin'],
	accumulation_distribution: ['ad', 'a/d', 'accumulation', 'distribution', 'adl'],
	vwap: ['volume weighted average price'],
	force_index: ['force', 'elder'],
	zscore: ['z', 'z-score', 'zscore', 'standard score'],
	percent_rank: ['percentile', 'rank']
};

function categoryLabel(category: IndicatorCategory): string {
	return INDICATOR_CATEGORIES.find((item) => item.id === category)?.label ?? category;
}

function matchesTerm(entry: IndicatorCatalogEntry, term: string): boolean {
	const label = entry.label.toLowerCase();
	if (entry.kind.includes(term) || label.includes(term)) return true;
	if (entry.kind.replaceAll('_', ' ').includes(term)) return true;
	if (categoryLabel(entry.category).toLowerCase().startsWith(term)) return true;
	const aliases = KIND_ALIASES[entry.kind] ?? [];
	if (aliases.some((alias) => alias.split(/\s+/).some((word) => word.startsWith(term)))) {
		return true;
	}
	if (aliases.some((alias) => alias.startsWith(term))) return true;
	if (term.length < 3) return false;
	return entry.summary
		.toLowerCase()
		.split(/[^a-z0-9%]+/)
		.some((word) => word.startsWith(term));
}

/**
 * Kinds matching every whitespace-separated term of `query`, in catalog order.
 * Terms match the kind id, label, category, aliases, and (from three letters)
 * words of the one-line summary. A blank query returns every kind.
 */
export function searchIndicatorKinds(query: string): IndicatorKindValue[] {
	const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
	return INDICATOR_CATALOG.filter((entry) => terms.every((term) => matchesTerm(entry, term))).map(
		(entry) => entry.kind
	);
}

/** Matching kinds grouped by category in picker order; empty groups are dropped. */
export function groupedIndicatorKinds(
	query: string
): { category: IndicatorCategory; label: string; kinds: IndicatorKindValue[] }[] {
	const matches = new Set(searchIndicatorKinds(query));
	return INDICATOR_CATEGORIES.map((category) => ({
		category: category.id,
		label: category.label,
		kinds: INDICATOR_CATALOG.filter(
			(entry) => entry.category === category.id && matches.has(entry.kind)
		).map((entry) => entry.kind)
	})).filter((group) => group.kinds.length > 0);
}

/** The minimal indicator shape the label helpers read. */
export type DisplayableIndicator = {
	id: string;
	kind: string;
	input?: unknown;
	timeframe?: string;
	parameters: object;
	offset?: unknown;
};

function displayValue(value: unknown): string {
	if (isBlank(value)) return '?';
	return String(value);
}

function baseLabel(indicator: DisplayableIndicator): string {
	const entry = findCatalogEntry(indicator.kind);
	const clock =
		indicator.timeframe !== undefined && indicator.timeframe !== ''
			? ` @ ${indicator.timeframe}`
			: '';
	if (entry === undefined) return `${indicator.id}${clock}`;
	if (entry.kind === 'identity') {
		const field = typeof indicator.input === 'string' ? indicator.input : 'close';
		return `${field}${clock}`;
	}
	if (entry.kind === 'constant') return displayValue(readParameter(indicator.parameters, 'value'));
	const values = entry.parameters
		.filter((spec) => !(spec.optional && isBlank(readParameter(indicator.parameters, spec.name))))
		.map((spec) => displayValue(readParameter(indicator.parameters, spec.name)));
	if (
		entry.input_mode === 'configurable' &&
		typeof indicator.input === 'string' &&
		indicator.input !== 'close'
	) {
		values.push(indicator.input);
	}
	return `${entry.label}(${values.join(', ')})${clock}`;
}

/** `1 bar ago` / `N bars ago`, or an empty string for the current bar. */
export function offsetText(offset: number): string {
	if (offset <= 0) return '';
	return offset === 1 ? '1 bar ago' : `${offset} bars ago`;
}

function withOffset(label: string, indicator: DisplayableIndicator): string {
	const lag = offsetText(offsetOf(indicator));
	return lag === '' ? label : `${label} · ${lag}`;
}

/**
 * Readable indicator label: `Supertrend(10, 3)`, `SMA(50, high)` for a non-close
 * source, the field name for identity, the level for constant, ` @ 4h` for an extra
 * clock, and ` · N bars ago` for an offset.
 */
export function indicatorDisplayLabel(indicator: DisplayableIndicator): string {
	return withOffset(baseLabel(indicator), indicator);
}

/** Readable operand label with its series: `Supertrend(10, 3) · direction`. */
export function operandDisplayLabel(indicator: DisplayableIndicator, series?: string): string {
	const base = baseLabel(indicator);
	return withOffset(series === undefined ? base : `${base} · ${series}`, indicator);
}
