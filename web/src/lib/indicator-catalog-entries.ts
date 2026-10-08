/**
 * The validated catalog and its lookups: entries by kind, labels, builder defaults,
 * and untyped parameter reads and writes. Re-exported by `indicator-catalog.ts`.
 */
import catalogDocument from './generated/indicator-catalog.json';
import { parseIndicatorCatalog } from './indicator-catalog-parse';
import type {
	IndicatorCatalogEntry,
	IndicatorField,
	IndicatorKindValue,
	IndicatorParameterName,
	IndicatorParameters
} from './indicator-catalog-types';

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

export function isBlank(value: unknown): boolean {
	return value === undefined || value === null || value === '';
}

export function numberOf(parameters: object, name: string): number {
	const value = readParameter(parameters, name);
	return typeof value === 'number' ? value : Number(value);
}
