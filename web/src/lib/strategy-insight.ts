import type { BuilderModel, ConditionDraft } from './strategies';

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
			left: { indicator?: string; literal?: string };
			operator: string;
			right: { indicator?: string; literal?: string };
		};
		const left = comparison.left.indicator ?? comparison.left.literal ?? '?';
		const right = comparison.right.indicator ?? comparison.right.literal ?? '?';
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
	const entryText = conditionToText(model.entry.when);
	return [
		`${model.name}: when ${entryText}, enter long on ${model.product_id} ${model.timeframe}.`,
		`Risk ${model.sizing.risk_fraction} of equity per trade between $${model.sizing.min_quote_notional} and $${model.sizing.max_quote_notional}.`,
		`Initial stop ${model.exits.initial_stop.multiple}× ATR, take profit at ${model.exits.take_profit.multiple}× risk, time exit after ${model.exits.time_exit.max_bars_held} bars.`
	].join(' ');
}

const INDICATOR_ID_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const MAX_CONDITION_DEPTH = 4;
const MAX_CONDITION_NODES = 64;

type IndicatorLike = { id: string; kind: string; input: unknown; parameters: { period: number } };

function indicatorInputMatchesKind(indicator: IndicatorLike): boolean {
	if (indicator.kind === 'atr') {
		return (
			Array.isArray(indicator.input) &&
			indicator.input.length === 3 &&
			indicator.input[0] === 'high' &&
			indicator.input[1] === 'low' &&
			indicator.input[2] === 'close'
		);
	}
	if (indicator.kind === 'volume_sma') return indicator.input === 'volume';
	return indicator.input === 'close';
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
		if (!indicatorInputMatchesKind(indicator)) {
			problems.push(
				`Indicator "${indicator.id}" has the wrong input for ${indicator.kind}; switch kinds or re-add it.`
			);
		}
		const period = Number(indicator.parameters.period);
		const maximum = indicator.kind === 'rsi' || indicator.kind === 'atr' ? 100 : 500;
		if (!Number.isInteger(period) || period < 2 || period > maximum) {
			problems.push(
				`Indicator "${indicator.id}" period must be an integer between 2 and ${maximum}.`
			);
		}
		warmupNeeded.set(indicator.id, indicator.kind === 'rsi' ? period + 1 : period);
	}
	problems.push(...validateCondition(model.entry.when, ids));
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
		.filter((indicator) => indicator.kind === 'atr')
		.map((indicator) => indicator.id);
	if (!atrIds.includes(model.exits.initial_stop.atr_indicator)) {
		problems.push('The initial stop must reference a defined ATR indicator.');
	}
	if (!Number.isInteger(model.warmup_bars) || model.warmup_bars < 1 || model.warmup_bars > 10_000) {
		problems.push('Warmup must be an integer between 1 and 10,000 bars.');
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

function validateCondition(condition: ConditionDraft, ids: Set<string>): string[] {
	const problems: string[] = [];
	validateConditionShape(condition, ids, problems);
	const { nodes, depth } = measureCondition(condition);
	if (depth > MAX_CONDITION_DEPTH) {
		problems.push(`Entry condition nesting must stay at or below ${MAX_CONDITION_DEPTH} levels.`);
	}
	if (nodes > MAX_CONDITION_NODES) {
		problems.push(`Entry condition tree must stay at or below ${MAX_CONDITION_NODES} nodes.`);
	}
	return problems;
}

function validateConditionShape(
	condition: ConditionDraft,
	ids: Set<string>,
	problems: string[]
): void {
	if (isComparison(condition)) {
		const comparison = condition as {
			left: { indicator?: string; literal?: string };
			operator: string;
			right: { indicator?: string; literal?: string };
		};
		if (comparison.left.indicator !== undefined && !ids.has(comparison.left.indicator)) {
			problems.push(`Entry references unknown indicator "${comparison.left.indicator}".`);
		}
		if (comparison.right.indicator !== undefined && !ids.has(comparison.right.indicator)) {
			problems.push(`Entry references unknown indicator "${comparison.right.indicator}".`);
		}
		if (comparison.left.literal !== undefined && !DECIMAL_PATTERN.test(comparison.left.literal)) {
			problems.push('Entry literals must be exact decimal numbers.');
		}
		if (comparison.right.literal !== undefined && !DECIMAL_PATTERN.test(comparison.right.literal)) {
			problems.push('Entry literals must be exact decimal numbers.');
		}
		if (
			(comparison.operator === 'crosses_above' || comparison.operator === 'crosses_below') &&
			(comparison.left.indicator === undefined || comparison.right.indicator === undefined)
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
		for (const child of children) validateConditionShape(child, ids, problems);
		return;
	}
	validateConditionShape((condition as { not: ConditionDraft }).not, ids, problems);
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

export type EngineSupportRow = { label: string; v1: boolean; v2: boolean; note: string };

// The current bar-backtest engine consumes these settings. It fills every
// entry at the next bar open unconditionally, so cooldown and execution
// preferences are declared by the schema but not modeled by the engine.
export const ENGINE_SUPPORT: EngineSupportRow[] = [
	{
		label: 'Entry conditions (ALL / ANY / NOT, comparisons, crossovers)',
		v1: true,
		v2: true,
		note: 'evaluated on completed candles, no lookahead'
	},
	{
		label: 'Indicators: EMA, SMA, RSI, ATR, volume SMA',
		v1: true,
		v2: true,
		note: 'exact Decimal arithmetic'
	},
	{
		label: 'Risk-fraction sizing with notional bounds',
		v1: true,
		v2: true,
		note: 'bounded by exposure fraction'
	},
	{
		label: 'ATR initial stop',
		v1: true,
		v2: true,
		note: 'stop-loss priority inside the bar'
	},
	{
		label: 'Reward/risk take profit',
		v1: true,
		v2: true,
		note: 'checked after the stop'
	},
	{
		label: 'Time exit (max bars held)',
		v1: true,
		v2: true,
		note: 'exits at the open'
	},
	{
		label: 'Constant spread stress assumption',
		v1: false,
		v2: true,
		note: 'V2 models an explicit total bid-ask spread; V1 does not'
	},
	{
		label: 'Entry cooldown (cooldown_bars)',
		v1: false,
		v2: false,
		note: 'not modeled by either bar backtester'
	},
	{
		label: 'Maker-only / marketable entry preference',
		v1: false,
		v2: false,
		note: 'fills at next open; no order book'
	},
	{
		label: 'Entry wait and unfilled policy',
		v1: false,
		v2: false,
		note: 'not modeled by either bar backtester'
	},
	{
		label: 'Trailing stop',
		v1: false,
		v2: false,
		note: 'the published strategy profile permits disabled only'
	}
];
