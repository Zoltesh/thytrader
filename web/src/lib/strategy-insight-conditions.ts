/**
 * Rule-tree (condition) structure for the strategy builder: comparison / group
 * checks, the largest operand lag per indicator, and the shape, operand-series,
 * depth and size checks every rule tree passes. Re-exported by `strategy-insight.ts`.
 */
import { MAX_INDICATOR_OFFSET, isValidOffset } from './indicator-catalog';
import {
	INDICATOR_OUTPUT_SERIES,
	type ConditionDraft,
	type IndicatorKindValue,
	type OperandDraft
} from './strategies';

export const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

const MAX_CONDITION_DEPTH = 4;
const MAX_CONDITION_NODES = 64;

export type IndicatorLike = {
	id: string;
	kind: string;
	input?: unknown;
	timeframe?: string;
	offset?: unknown;
	parameters: object;
};

export function isComparison(condition: ConditionDraft): boolean {
	return 'operator' in condition;
}

export function isGroup(condition: ConditionDraft): boolean {
	return 'all' in condition || 'any' in condition;
}

/** Largest requested native-clock lag of one indicator in a rule tree. */
export function conditionOperandOffset(condition: ConditionDraft, id: string): number {
	if ('left' in condition) {
		return Math.max(
			0,
			...[condition.left, condition.right].map((operand) =>
				'indicator' in operand && operand.indicator === id && isValidOffset(operand.offset)
					? (operand.offset ?? 0)
					: 0
			)
		);
	}
	if ('not' in condition) return conditionOperandOffset(condition.not, id);
	const children = 'all' in condition ? condition.all : condition.any;
	return Math.max(0, ...children.map((child) => conditionOperandOffset(child, id)));
}

export function maximumOperandOffset(conditions: ConditionDraft[], id: string): number {
	return Math.max(0, ...conditions.map((condition) => conditionOperandOffset(condition, id)));
}

export function validateCondition(
	condition: ConditionDraft,
	indicators: IndicatorLike[],
	label: string
): string[] {
	const problems: string[] = [];
	const ids = new Set(indicators.map((indicator) => indicator.id));
	const byId = new Map(indicators.map((indicator) => [indicator.id, indicator]));
	validateConditionShape(condition, ids, byId, problems, label);
	const { nodes, depth } = measureCondition(condition);
	if (depth > MAX_CONDITION_DEPTH) {
		problems.push(
			`${label} condition nesting must stay at or below ${MAX_CONDITION_DEPTH} levels.`
		);
	}
	if (nodes > MAX_CONDITION_NODES) {
		problems.push(`${label} condition tree must stay at or below ${MAX_CONDITION_NODES} nodes.`);
	}
	return problems;
}

function validateConditionShape(
	condition: ConditionDraft,
	ids: Set<string>,
	byId: Map<string, IndicatorLike>,
	problems: string[],
	label: string
): void {
	if (isComparison(condition)) {
		const comparison = condition as {
			left: OperandDraft;
			operator: string;
			right: OperandDraft;
		};
		validateOperandSeries(comparison.left, ids, byId, problems, label);
		validateOperandSeries(comparison.right, ids, byId, problems, label);
		if ('literal' in comparison.left && !DECIMAL_PATTERN.test(comparison.left.literal)) {
			problems.push(`${label} literals must be exact decimal numbers.`);
		}
		if ('literal' in comparison.right && !DECIMAL_PATTERN.test(comparison.right.literal)) {
			problems.push(`${label} literals must be exact decimal numbers.`);
		}
		if (
			(comparison.operator === 'crosses_above' || comparison.operator === 'crosses_below') &&
			(!('indicator' in comparison.left) || !('indicator' in comparison.right))
		) {
			problems.push('Crossover rules must compare two indicators.');
		}
		return;
	}
	if (isGroup(condition)) {
		const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] };
		const children = group.all ?? group.any ?? [];
		if (children.length === 0) problems.push('Empty condition groups are not allowed.');
		if (children.length > 20) problems.push('Condition groups must hold at most 20 children.');
		for (const child of children) validateConditionShape(child, ids, byId, problems, label);
		return;
	}
	validateConditionShape((condition as { not: ConditionDraft }).not, ids, byId, problems, label);
}

function validateOperandSeries(
	operand: OperandDraft,
	ids: Set<string>,
	byId: Map<string, IndicatorLike>,
	problems: string[],
	label: string
): void {
	if (!('indicator' in operand)) return;
	if (!ids.has(operand.indicator)) {
		problems.push(`${label} references unknown indicator "${operand.indicator}".`);
		return;
	}
	const indicator = byId.get(operand.indicator);
	if (indicator === undefined) return;
	if (operand.offset !== undefined) {
		if (!isValidOffset(operand.offset)) {
			problems.push(
				`${label} operand offset must be a whole number between 0 and ${MAX_INDICATOR_OFFSET}.`
			);
		} else if (indicator.kind === 'constant' && operand.offset !== 0) {
			problems.push(`${label} constant operand must omit offset.`);
		}
	}
	const outputs = INDICATOR_OUTPUT_SERIES[indicator.kind as IndicatorKindValue];
	if (outputs === undefined) {
		if (operand.series !== undefined) {
			problems.push(`${label} "${operand.indicator}" is single-output and must omit series.`);
		}
		return;
	}
	if (operand.series === undefined || !outputs.includes(operand.series)) {
		problems.push(`${label} "${operand.indicator}" series must be one of ${outputs.join(', ')}.`);
	}
}

function measureCondition(condition: ConditionDraft): { nodes: number; depth: number } {
	if (isComparison(condition)) return { nodes: 1, depth: 1 };
	if (isGroup(condition)) {
		const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] };
		const children = group.all ?? group.any ?? [];
		let nodes = 1;
		let depth = 0;
		for (const child of children) {
			const measured = measureCondition(child);
			nodes += measured.nodes;
			depth = Math.max(depth, measured.depth);
		}
		return { nodes, depth: depth + 1 };
	}
	const inner = measureCondition((condition as { not: ConditionDraft }).not);
	return { nodes: inner.nodes + 1, depth: inner.depth + 1 };
}
