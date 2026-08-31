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
		return children.map(conditionToText).join(joiner);
	}
	return `NOT (${conditionToText((condition as { not: ConditionDraft }).not)})`;
}

export function plainEnglishSummary(model: BuilderModel): string {
	const entryText = conditionToText(model.entry.when);
	return [
		`${model.name}: when ${entryText}, enter long on ${model.product_id} ${model.timeframe}.`,
		`Risk ${model.sizing.risk_fraction} of equity per trade between $${model.sizing.min_quote_notional} and $${model.sizing.max_quote_notional}.`,
		`Initial stop ${model.exits.initial_stop.multiple}× ATR, take profit at ${model.exits.take_profit.multiple}× risk, time exit after ${model.exits.time_exit.max_bars_held} bars.`
	].join(' ');
}

export function validateDefinition(model: BuilderModel): string[] {
	const problems: string[] = [];
	if (model.name.trim().length === 0) problems.push('Name is required.');
	if (model.indicators.length === 0) problems.push('At least one indicator is required.');
	const ids = new Set(model.indicators.map((indicator) => indicator.id));
	if (ids.size !== model.indicators.length) problems.push('Indicator identifiers must be unique.');
	if (
		model.indicators.some(
			(indicator) => indicator.parameters.period < 2 || indicator.parameters.period > 500
		)
	) {
		problems.push('Indicator periods must be between 2 and 500 (RSI/ATR max 100).');
	}
	problems.push(...validateCondition(model.entry.when, ids));
	for (const field of ['risk_fraction', 'min_quote_notional', 'max_quote_notional'] as const) {
		if (!/^\d+(\.\d+)?$/.test(model.sizing[field]) || Number(model.sizing[field]) < 0) {
			problems.push(`Sizing ${field.replace('_', ' ')} must be a non-negative decimal.`);
		}
	}
	if (!/^\d*\.?\d+$/.test(model.portfolio_limits.max_strategy_exposure_fraction)) {
		problems.push('Max strategy exposure must be a non-negative decimal.');
	}
	if (model.exits.initial_stop.multiple !== '' && Number(model.exits.initial_stop.multiple) <= 0) {
		problems.push('Initial stop multiple must be positive.');
	}
	if (model.exits.take_profit.multiple !== '' && Number(model.exits.take_profit.multiple) <= 0) {
		problems.push('Take profit multiple must be positive.');
	}
	if (model.exits.time_exit.max_bars_held < 1)
		problems.push('Time exit must hold at least one bar.');
	if (model.warmup_bars < 1) problems.push('Warmup must be at least one bar.');
	return problems;
}

function validateCondition(condition: ConditionDraft, ids: Set<string>): string[] {
	const problems: string[] = [];
	if (isComparison(condition)) {
		const comparison = condition as {
			left: { indicator?: string; literal?: string };
			right: { indicator?: string; literal?: string };
		};
		if (comparison.left.indicator !== undefined && !ids.has(comparison.left.indicator)) {
			problems.push(`Entry references unknown indicator "${comparison.left.indicator}".`);
		}
		if (comparison.right.indicator !== undefined && !ids.has(comparison.right.indicator)) {
			problems.push(`Entry references unknown indicator "${comparison.right.indicator}".`);
		}
		if (comparison.left.literal !== undefined && !/^-?\d+(\.\d+)?$/.test(comparison.left.literal)) {
			problems.push('Entry literals must be exact decimal numbers.');
		}
		if (
			comparison.right.literal !== undefined &&
			!/^-?\d+(\.\d+)?$/.test(comparison.right.literal)
		) {
			problems.push('Entry literals must be exact decimal numbers.');
		}
		return problems;
	}
	if (isGroup(condition)) {
		const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] };
		const children = group.all ?? group.any ?? [];
		if (children.length === 0) problems.push('Empty condition groups are not allowed.');
		for (const child of children) problems.push(...validateCondition(child, ids));
		return problems;
	}
	return validateCondition((condition as { not: ConditionDraft }).not, ids);
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
