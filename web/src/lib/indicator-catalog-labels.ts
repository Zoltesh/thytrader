/**
 * Readable indicator and operand labels such as `Supertrend(10, 3) · direction`.
 * Re-exported by `indicator-catalog.ts`.
 */
import { findCatalogEntry, isBlank, readParameter } from './indicator-catalog-entries';
import { offsetOf } from './indicator-catalog-warmup';

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
