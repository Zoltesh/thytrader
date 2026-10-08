/**
 * Pure helpers of the definition form's ALL / ANY / NOT rule tree: row
 * keywords, node kinds, operand select keys and labels, and the grouped
 * operand options. Everything that edits the tree lives in `RuleTree.svelte`.
 */
import {
	indicatorOperandKey,
	operandChoices,
	type ConditionDraft,
	type IndicatorDraft,
	type OperandChoice,
	type ReferenceInstrumentDraft
} from '$lib/strategies';

export const CONDITION_OPERATORS: { value: string; label: string }[] = [
	{ value: 'crosses_above', label: 'crosses above' },
	{ value: 'crosses_below', label: 'crosses below' },
	{ value: 'greater_than', label: '>' },
	{ value: 'greater_than_or_equal', label: '≥' },
	{ value: 'less_than', label: '<' },
	{ value: 'less_than_or_equal', label: '≤' },
	{ value: 'equals', label: '=' }
];

/** Row keyword for a child of a group: IF for the first, then AND / OR. */
export function rowKeyword(parent: object, index: number): string {
	const container = parent as { all?: unknown[]; any?: unknown[]; not?: unknown };
	if (container.not !== undefined) return 'NOT';
	if (index === 0) return 'IF';
	return container.any !== undefined ? 'OR' : 'AND';
}

export function isGroup(condition: ConditionDraft): boolean {
	return 'all' in condition || 'any' in condition;
}

export function isNot(condition: ConditionDraft): boolean {
	return 'not' in condition;
}

export function childIndex(condition: ConditionDraft, parent: object): number {
	const container = parent as {
		all?: ConditionDraft[];
		any?: ConditionDraft[];
		not?: ConditionDraft;
	};
	if (container.all) return container.all.indexOf(condition);
	if (container.any) return container.any.indexOf(condition);
	return -1;
}

export function isRootGroup(condition: ConditionDraft, root: ConditionDraft): boolean {
	return condition === root;
}

export function rightOperandKey(comparison: {
	right: { indicator?: string; series?: string; literal?: string };
}): string {
	return comparison.right.indicator !== undefined
		? indicatorOperandKey({
				indicator: comparison.right.indicator,
				series: comparison.right.series
			})
		: 'literal';
}

export function leftOperandKey(comparison: {
	left: { indicator?: string; series?: string; literal?: string };
}): string {
	return comparison.left.indicator !== undefined
		? indicatorOperandKey({
				indicator: comparison.left.indicator,
				series: comparison.left.series
			})
		: 'literal';
}

export function operandLabel(operand: { indicator?: string; series?: string }): string {
	if (operand.indicator === undefined) return '';
	return operand.series === undefined
		? operand.indicator
		: `${operand.indicator}.${operand.series}`;
}

/**
 * Operand select blocks: single-output indicators as plain options and each
 * multi-series indicator as one `<optgroup>` of its series (keys unchanged).
 */
export function operandOptionBlocks(
	indicators: IndicatorDraft[],
	references: ReferenceInstrumentDraft[]
): { key: string; group?: string; choices: OperandChoice[] }[] {
	const blocks: { key: string; group?: string; choices: OperandChoice[] }[] = [];
	for (const choice of operandChoices(indicators, references)) {
		const last = blocks.at(-1);
		if (choice.group !== undefined && last !== undefined && last.group === choice.group) {
			last.choices.push(choice);
			continue;
		}
		blocks.push({ key: choice.key, group: choice.group, choices: [choice] });
	}
	return blocks;
}
