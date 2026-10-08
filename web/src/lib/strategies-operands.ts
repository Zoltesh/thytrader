/**
 * Builder condition and operand drafts: operand select keys, operand choices, and
 * reference-aware operand labels. Re-exported by `strategies.ts`.
 */
import { indicatorDisplayLabel, operandDisplayLabel } from '$lib/indicator-catalog';
import {
	INDICATOR_OUTPUT_SERIES,
	type IndicatorDraft,
	type ReferenceInstrumentDraft
} from './strategies-indicators';

export type ComparisonOperatorValue =
	| 'greater_than'
	| 'greater_than_or_equal'
	| 'less_than'
	| 'less_than_or_equal'
	| 'equals'
	| 'crosses_above'
	| 'crosses_below';

export type OperandDraft =
	{ indicator: string; series?: string; offset?: number } | { literal: string };

export type ConditionDraft =
	| { left: OperandDraft; operator: ComparisonOperatorValue; right: OperandDraft }
	| { all: ConditionDraft[] }
	| { any: ConditionDraft[] }
	| { not: ConditionDraft };

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
