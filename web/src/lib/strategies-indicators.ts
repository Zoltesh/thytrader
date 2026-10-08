/**
 * Builder indicator drafts: kind and input options from the generated catalog,
 * per-kind defaults, and the canonical saved payload. Re-exported by `strategies.ts`.
 */
import {
	catalogEntry,
	defaultParameters,
	findCatalogEntry,
	INDICATOR_CATALOG,
	type IndicatorCatalogEntry,
	type IndicatorField,
	type IndicatorKindValue,
	type IndicatorParameters,
	isValidOffset,
	isValidParameterValue,
	parameterProblems,
	readParameter,
	writeParameter
} from '$lib/indicator-catalog';

/** One OHLCV field: an identity source or a configurable rolling input. */
export type IdentityInput = IndicatorField;

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
