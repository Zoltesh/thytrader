/**
 * Validation of the generated catalog document (`generated/indicator-catalog.json`):
 * every field is type-checked once at load. Re-exported by `indicator-catalog.ts`.
 */
import {
	INDICATOR_CATEGORIES,
	INDICATOR_KINDS,
	INDICATOR_PARAMETER_NAMES,
	type IndicatorCatalogEntry,
	type IndicatorCategory,
	type IndicatorField,
	type IndicatorInputMode,
	type IndicatorParameterSpec
} from './indicator-catalog-types';

const FIELDS: readonly IndicatorField[] = ['open', 'high', 'low', 'close', 'volume'];
const CATEGORY_IDS: readonly IndicatorCategory[] = INDICATOR_CATEGORIES.map(
	(category) => category.id
);
const INPUT_MODES: readonly IndicatorInputMode[] = ['configurable', 'locked', 'none'];
const VALUE_TYPES: readonly IndicatorParameterSpec['value_type'][] = ['integer', 'decimal'];

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
