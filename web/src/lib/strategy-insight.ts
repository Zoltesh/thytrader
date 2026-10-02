import {
	MAX_INDICATOR_OFFSET,
	findCatalogEntry,
	indicatorWarmupBars,
	inputMatchesKind,
	isValidOffset,
	parameterProblems
} from './indicator-catalog';
import {
	INDICATOR_OUTPUT_SERIES,
	extraIndicatorTimeframes,
	quoteLabelFor,
	resolvedIndicatorTimeframe,
	validHtfTimeframes,
	type BuilderModel,
	type ConditionDraft,
	type HtfFilterDraft,
	type IndicatorDraft,
	type IndicatorKindValue,
	type OperandDraft
} from './strategies';

export const OPERATOR_LABELS: Record<string, string> = {
	crosses_above: 'crosses above',
	crosses_below: 'crosses below',
	greater_than: '>',
	greater_than_or_equal: '≥',
	less_than: '<',
	less_than_or_equal: '≤',
	equals: '='
};

function isComparison(condition: ConditionDraft): boolean {
	return 'operator' in condition;
}

function isGroup(condition: ConditionDraft): boolean {
	return 'all' in condition || 'any' in condition;
}

export function conditionToText(condition: ConditionDraft): string {
	if (isComparison(condition)) {
		const comparison = condition as {
			left: OperandDraft;
			operator: string;
			right: OperandDraft;
		};
		const left = operandText(comparison.left);
		const right = operandText(comparison.right);
		const symbol = OPERATOR_LABELS[comparison.operator] ?? comparison.operator;
		return `${left} ${symbol} ${right}`;
	}
	if (isGroup(condition)) {
		const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] };
		const children = group.all ?? group.any ?? [];
		const joiner = group.all ? ' AND ' : ' OR ';
		// Parenthesize nested groups so the rendered text stays unambiguous.
		return children.map((child) => renderConditionChild(child, joiner)).join(joiner);
	}
	return `NOT (${conditionToText((condition as { not: ConditionDraft }).not)})`;
}

function operandText(operand: OperandDraft): string {
	if ('literal' in operand) return operand.literal;
	return operand.series === undefined
		? operand.indicator
		: `${operand.indicator}.${operand.series}`;
}

function renderConditionChild(child: ConditionDraft, parentJoiner: string): string {
	if (!isGroup(child)) return conditionToText(child);
	const childJoiner =
		(child as { all?: ConditionDraft[]; any?: ConditionDraft[] }).all !== undefined
			? ' AND '
			: ' OR ';
	if (childJoiner === parentJoiner) return conditionToText(child);
	return `(${conditionToText(child)})`;
}

export function plainEnglishSummary(model: BuilderModel): string {
	const quote = quoteLabelFor(model.product_id);
	const entryText = conditionToText(model.entry.when);
	const htf =
		model.htf_filter === null
			? ''
			: ` HTF filter on ${model.htf_filter.timeframe}: ${conditionToText(model.htf_filter.when)}.`;
	return [
		`${model.name}: when ${entryText}, enter long on ${model.product_id} ${model.timeframe}.${htf}`,
		`Risk ${model.sizing.risk_fraction} of equity per trade between ${model.sizing.min_quote_notional} ${quote} and ${model.sizing.max_quote_notional} ${quote}.`,
		`Initial stop ${model.exits.initial_stop.multiple}× ATR, take profit at ${model.exits.take_profit.multiple}× risk, time exit after ${model.exits.time_exit.max_bars_held} bars.`
	].join(' ');
}

export function requiredDataText(model: BuilderModel): string {
	const parts = [
		`${model.warmup_bars} completed ${model.timeframe} bars (OHLCV) before the first signal.`
	];
	if (model.htf_filter !== null) {
		parts.push(
			`Also ${model.htf_filter.warmup_bars} completed ${model.htf_filter.timeframe} HTF bars, using only the last completed HTF bar at each LTF close.`
		);
	}
	const extra = extraIndicatorTimeframes(model.indicators, model.timeframe).filter(
		(timeframe) => timeframe !== model.htf_filter?.timeframe
	);
	if (extra.length > 0) {
		parts.push(
			`Extra indicator clocks ${extra.join(', ')} use last-completed bars of those timeframes.`
		);
	}
	if (model.htf_filter !== null || extra.length > 0) {
		parts.push('Research, paper, and live share that alignment.');
	}
	return parts.join(' ');
}

const INDICATOR_ID_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const MAX_CONDITION_DEPTH = 4;
const MAX_CONDITION_NODES = 64;

type IndicatorLike = {
	id: string;
	kind: string;
	input?: unknown;
	timeframe?: string;
	offset?: unknown;
	parameters: object;
};

/**
 * Catalog-driven shape checks for one declaration: the kind exists, the input is
 * what the kind accepts, every parameter is in bounds and its constraints hold, and
 * timeframe / offset only appear on kinds that take them.
 */
function validateIndicatorShape(indicator: IndicatorLike, label: string): string[] {
	const subject = `${label} "${indicator.id}"`;
	const entry = findCatalogEntry(indicator.kind);
	if (entry === undefined) {
		return [`${subject} uses an unknown indicator kind "${indicator.kind}".`];
	}
	const problems: string[] = [];
	if (!inputMatchesKind(entry, indicator.input)) {
		problems.push(
			`${subject} has the wrong input for ${indicator.kind}; switch kinds or re-add it.`
		);
	}
	problems.push(...parameterProblems(entry.kind, indicator.parameters, subject));
	if (
		!entry.supports_timeframe &&
		indicator.timeframe !== undefined &&
		indicator.timeframe !== ''
	) {
		problems.push(`${subject} ${indicator.kind} must omit timeframe.`);
	}
	if (indicator.offset !== undefined && indicator.offset !== null) {
		if (!entry.supports_offset) {
			problems.push(`${subject} ${indicator.kind} must omit offset.`);
		} else if (!isValidOffset(indicator.offset)) {
			problems.push(
				`${subject} offset must be a whole number of bars between 0 and ${MAX_INDICATOR_OFFSET}.`
			);
		}
	}
	return problems;
}

export function validateDefinition(model: BuilderModel): string[] {
	const problems: string[] = [];
	if (model.name.trim().length === 0) problems.push('Name is required.');
	if (model.name.length > 120) problems.push('Name must be at most 120 characters.');
	if (model.description.length > 500) problems.push('Description must be at most 500 characters.');
	if (model.indicators.length === 0) problems.push('At least one indicator is required.');
	if (model.indicators.length > 20) problems.push('At most 20 indicators are allowed.');
	const ids = new Set(model.indicators.map((indicator) => indicator.id));
	if (ids.size !== model.indicators.length) problems.push('Indicator identifiers must be unique.');
	const warmupNeeded = new Map<string, number>();
	for (const indicator of model.indicators) {
		if (!INDICATOR_ID_PATTERN.test(indicator.id)) {
			problems.push(
				`Indicator id "${indicator.id}" must start with a lowercase letter and use lowercase letters, digits, or underscores.`
			);
		}
		problems.push(...validateIndicatorShape(indicator, 'Indicator'));
		const clock = resolvedIndicatorTimeframe(indicator, model.timeframe);
		if (clock !== model.timeframe && !validHtfTimeframes(model.timeframe).includes(clock)) {
			problems.push(
				`Indicator "${indicator.id}" timeframe ${clock} must be a coarser integer multiple of ${model.timeframe}.`
			);
		}
		if (clock === model.timeframe) {
			warmupNeeded.set(indicator.id, indicatorWarmupBars(indicator));
		}
	}
	problems.push(...validateCondition(model.entry.when, model.indicators, 'Entry'));
	if (model.htf_filter !== null) {
		problems.push(...validateHtfFilter(model.htf_filter, ids, model.timeframe, model.indicators));
	}
	const warmupRequirement = Math.max(0, ...warmupNeeded.values());
	if (Number(model.warmup_bars) < warmupRequirement) {
		problems.push(
			`Warmup must cover the longest indicator period (at least ${warmupRequirement} bars).`
		);
	}
	if (!DECIMAL_PATTERN.test(model.sizing.risk_fraction)) {
		problems.push('Risk fraction must be a plain decimal number.');
	} else if (Number(model.sizing.risk_fraction) <= 0 || Number(model.sizing.risk_fraction) > 0.25) {
		problems.push('Risk fraction must be greater than 0 and at most 0.25.');
	}
	for (const field of ['min_quote_notional', 'max_quote_notional'] as const) {
		if (!DECIMAL_PATTERN.test(model.sizing[field])) {
			problems.push(`Sizing ${field.replace(/_/g, ' ')} must be a plain decimal number.`);
		} else if (Number(model.sizing[field]) <= 0) {
			problems.push(`Sizing ${field.replace(/_/g, ' ')} must be greater than zero.`);
		}
	}
	if (
		DECIMAL_PATTERN.test(model.sizing.min_quote_notional) &&
		DECIMAL_PATTERN.test(model.sizing.max_quote_notional) &&
		Number(model.sizing.min_quote_notional) > Number(model.sizing.max_quote_notional)
	) {
		problems.push('Minimum notional must not exceed maximum notional.');
	}
	if (!DECIMAL_PATTERN.test(model.portfolio_limits.max_strategy_exposure_fraction)) {
		problems.push('Max strategy exposure must be a plain decimal number.');
	} else if (
		Number(model.portfolio_limits.max_strategy_exposure_fraction) <= 0 ||
		Number(model.portfolio_limits.max_strategy_exposure_fraction) > 1
	) {
		problems.push('Max strategy exposure must be greater than 0 and at most 1.');
	}
	validateMultiple(
		model.exits.initial_stop.multiple,
		'Initial stop ATR multiple',
		0.5,
		10,
		problems
	);
	validateMultiple(
		model.exits.take_profit.multiple,
		'Take profit reward/risk multiple',
		0.5,
		10,
		problems
	);
	if (
		!Number.isInteger(model.exits.time_exit.max_bars_held) ||
		model.exits.time_exit.max_bars_held < 1
	) {
		problems.push('Time exit must hold at least one bar.');
	}
	const atrIds = model.indicators
		.filter(
			(indicator) =>
				indicator.kind === 'atr' &&
				resolvedIndicatorTimeframe(indicator, model.timeframe) === model.timeframe
		)
		.map((indicator) => indicator.id);
	if (!atrIds.includes(model.exits.initial_stop.atr_indicator)) {
		problems.push('The initial stop must reference a defined ATR indicator.');
	}
	if (
		model.exits.trailing_stop.enabled &&
		!atrIds.includes(model.exits.trailing_stop.atr_indicator)
	) {
		problems.push(
			'The trailing stop must reference a defined ATR indicator on the decision clock.'
		);
	}
	if (!Number.isInteger(model.warmup_bars) || model.warmup_bars < 1 || model.warmup_bars > 10_000) {
		problems.push('Warmup must be an integer between 1 and 10,000 bars.');
	}
	return problems;
}

function validateHtfFilter(
	filter: HtfFilterDraft,
	decisionIds: Set<string>,
	decisionTimeframe: string,
	decisionIndicators: IndicatorDraft[]
): string[] {
	const problems: string[] = [];
	if (!validHtfTimeframes(decisionTimeframe).includes(filter.timeframe)) {
		problems.push(
			`HTF timeframe ${filter.timeframe} must be a coarser integer multiple of ${decisionTimeframe}.`
		);
	}
	if (filter.indicators.length === 0) problems.push('HTF filter requires at least one indicator.');
	if (filter.indicators.length > 20) problems.push('At most 20 HTF indicators are allowed.');
	const htfIds = new Set(filter.indicators.map((indicator) => indicator.id));
	if (htfIds.size !== filter.indicators.length) {
		problems.push('HTF indicator identifiers must be unique.');
	}
	for (const id of htfIds) {
		if (decisionIds.has(id)) {
			problems.push(`HTF indicator "${id}" must not reuse a decision indicator id.`);
		}
	}
	const warmupNeeded = new Map<string, number>();
	for (const indicator of filter.indicators) {
		if (!INDICATOR_ID_PATTERN.test(indicator.id)) {
			problems.push(
				`HTF indicator id "${indicator.id}" must start with a lowercase letter and use lowercase letters, digits, or underscores.`
			);
		}
		problems.push(...validateIndicatorShape(indicator, 'HTF indicator'));
		if (indicator.timeframe !== undefined && indicator.timeframe !== '') {
			problems.push(`HTF indicator "${indicator.id}" must omit timeframe.`);
		}
		warmupNeeded.set(indicator.id, indicatorWarmupBars(indicator));
	}
	problems.push(...validateCondition(filter.when, filter.indicators, 'HTF filter'));
	const extraOnHtf = decisionIndicators.filter(
		(indicator) => resolvedIndicatorTimeframe(indicator, decisionTimeframe) === filter.timeframe
	);
	for (const indicator of extraOnHtf) {
		warmupNeeded.set(`ltf:${indicator.id}`, indicatorWarmupBars(indicator));
	}
	const warmupRequirement = Math.max(0, ...warmupNeeded.values());
	if (Number(filter.warmup_bars) < warmupRequirement) {
		problems.push(
			`HTF warmup must cover the longest HTF indicator period (at least ${warmupRequirement} bars).`
		);
	}
	if (
		!Number.isInteger(filter.warmup_bars) ||
		filter.warmup_bars < 1 ||
		filter.warmup_bars > 10_000
	) {
		problems.push('HTF warmup must be an integer between 1 and 10,000 bars.');
	}
	return problems;
}

function validateMultiple(
	value: string,
	label: string,
	minimum: number,
	maximum: number,
	problems: string[]
): void {
	if (!DECIMAL_PATTERN.test(value)) {
		problems.push(`${label} must be a plain decimal number.`);
		return;
	}
	const parsed = Number(value);
	if (parsed < minimum || parsed > maximum) {
		problems.push(`${label} must be between ${minimum} and ${maximum}.`);
	}
}

function validateCondition(
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
