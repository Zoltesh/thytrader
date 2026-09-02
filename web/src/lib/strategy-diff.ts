import type { BuilderModel, ConditionDraft, IndicatorDraft } from './strategies';

export type FieldChange = {
	path: string;
	label: string;
	from: string;
	to: string;
	kind: 'changed' | 'added' | 'removed';
};

export type SemanticDiff = {
	changes: FieldChange[];
	summary: string;
};

const OPERATOR_LABELS: Record<string, string> = {
	crosses_above: 'crosses above',
	crosses_below: 'crosses below',
	greater_than: '>',
	greater_than_or_equal: '≥',
	less_than: '<',
	less_than_or_equal: '≤',
	equals: '='
};

const KIND_LABELS: Record<IndicatorDraft['kind'], string> = {
	ema: 'EMA',
	sma: 'SMA',
	rsi: 'RSI',
	atr: 'ATR',
	volume_sma: 'Volume SMA'
};

type ComparisonLike = {
	left: { indicator?: string; literal?: string };
	operator: string;
	right: { indicator?: string; literal?: string };
};

function isComparison(condition: ConditionDraft): boolean {
	return 'operator' in condition;
}

function isGroup(condition: ConditionDraft): boolean {
	return 'all' in condition || 'any' in condition;
}

export function conditionToText(condition: ConditionDraft): string {
	if (isComparison(condition)) {
		const comparison = condition as ComparisonLike;
		const left = comparison.left.indicator ?? comparison.left.literal ?? '?';
		const right = comparison.right.indicator ?? comparison.right.literal ?? '?';
		const symbol = OPERATOR_LABELS[comparison.operator] ?? comparison.operator;
		return `${left} ${symbol} ${right}`;
	}
	if (isGroup(condition)) {
		const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] };
		const children = group.all ?? group.any ?? [];
		const joiner = group.all !== undefined ? ' AND ' : ' OR ';
		return children.map(conditionToText).join(joiner);
	}
	const negated = condition as { not: ConditionDraft };
	return `NOT (${conditionToText(negated.not)})`;
}

function indicatorText(indicator: IndicatorDraft): string {
	return `${KIND_LABELS[indicator.kind]}(${indicator.parameters.period}) as "${indicator.id}"`;
}

const FIELD_LABELS: Record<string, string> = {
	name: 'Strategy name',
	description: 'Description',
	product_id: 'Market',
	timeframe: 'Timeframe',
	warmup_bars: 'Warmup bars',
	'entry.when': 'Entry conditions',
	cooldown_bars: 'Entry cooldown',
	'sizing.risk_fraction': 'Risk fraction per trade',
	'sizing.min_quote_notional': 'Minimum USD notional',
	'sizing.max_quote_notional': 'Maximum USD notional',
	max_strategy_exposure_fraction: 'Max strategy exposure',
	'exits.initial_stop.multiple': 'Initial stop (ATR multiple)',
	'exits.take_profit.multiple': 'Take profit (reward/risk)',
	'exits.time_exit.max_bars_held': 'Time exit (max bars held)',
	'trailing_stop.enabled': 'Trailing stop',
	'execution.entry_preference': 'Entry preference',
	'execution.max_entry_wait_bars': 'Max entry wait (bars)',
	'execution.on_unfilled_entry': 'On unfilled entry'
};

/**
 * Compare two builder models field by field and return human-readable
 * semantic changes. Condition trees compare by rendered text, not JSON
 * shape, so equivalent rewrites are not reported as changes.
 */
export function semanticDiff(before: BuilderModel, after: BuilderModel): SemanticDiff {
	const changes: FieldChange[] = [];
	const changed = (path: string, from: string, to: string): void => {
		if (from !== to) {
			changes.push({ path, label: FIELD_LABELS[path] ?? path, from, to, kind: 'changed' });
		}
	};

	changed('name', before.name, after.name);
	changed('description', before.description, after.description);
	changed('product_id', before.product_id, after.product_id);
	changed('timeframe', before.timeframe, after.timeframe);
	changed('warmup_bars', String(before.warmup_bars), String(after.warmup_bars));
	changed('entry.when', conditionToText(before.entry.when), conditionToText(after.entry.when));
	changed('cooldown_bars', String(before.cooldown_bars), String(after.cooldown_bars));
	changed('sizing.risk_fraction', before.sizing.risk_fraction, after.sizing.risk_fraction);
	changed(
		'sizing.min_quote_notional',
		before.sizing.min_quote_notional,
		after.sizing.min_quote_notional
	);
	changed(
		'sizing.max_quote_notional',
		before.sizing.max_quote_notional,
		after.sizing.max_quote_notional
	);
	changed(
		'max_strategy_exposure_fraction',
		before.portfolio_limits.max_strategy_exposure_fraction,
		after.portfolio_limits.max_strategy_exposure_fraction
	);
	changed(
		'exits.initial_stop.multiple',
		before.exits.initial_stop.multiple,
		after.exits.initial_stop.multiple
	);
	changed(
		'exits.take_profit.multiple',
		before.exits.take_profit.multiple,
		after.exits.take_profit.multiple
	);
	changed(
		'exits.time_exit.max_bars_held',
		String(before.exits.time_exit.max_bars_held),
		String(after.exits.time_exit.max_bars_held)
	);
	changed(
		'trailing_stop.enabled',
		before.exits.trailing_stop.enabled ? 'enabled' : 'disabled',
		after.exits.trailing_stop.enabled ? 'enabled' : 'disabled'
	);
	changed(
		'execution.entry_preference',
		before.execution.entry_preference,
		after.execution.entry_preference
	);
	changed(
		'execution.max_entry_wait_bars',
		String(before.execution.max_entry_wait_bars),
		String(after.execution.max_entry_wait_bars)
	);
	changed(
		'execution.on_unfilled_entry',
		before.execution.on_unfilled_entry,
		after.execution.on_unfilled_entry
	);

	const beforeIndicators = new Map(before.indicators.map((indicator) => [indicator.id, indicator]));
	const afterIndicators = new Map(after.indicators.map((indicator) => [indicator.id, indicator]));
	for (const [id, indicator] of afterIndicators) {
		const previous = beforeIndicators.get(id);
		if (previous === undefined) {
			changes.push({
				path: `indicators.${id}`,
				label: `Indicator "${id}"`,
				from: '',
				to: indicatorText(indicator),
				kind: 'added'
			});
		} else if (indicatorText(previous) !== indicatorText(indicator)) {
			changes.push({
				path: `indicators.${id}`,
				label: `Indicator "${id}"`,
				from: indicatorText(previous),
				to: indicatorText(indicator),
				kind: 'changed'
			});
		}
	}
	for (const [id, indicator] of beforeIndicators) {
		if (!afterIndicators.has(id)) {
			changes.push({
				path: `indicators.${id}`,
				label: `Indicator "${id}"`,
				from: indicatorText(indicator),
				to: '',
				kind: 'removed'
			});
		}
	}

	const order: Record<FieldChange['kind'], number> = { added: 0, changed: 1, removed: 2 };
	changes.sort((a, b) => order[a.kind] - order[b.kind] || a.path.localeCompare(b.path));
	const added = changes.filter((change) => change.kind === 'added').length;
	const removed = changes.filter((change) => change.kind === 'removed').length;
	const parts: string[] = [];
	if (added > 0) parts.push(`${added} added`);
	if (removed > 0) parts.push(`${removed} removed`);
	parts.push(`${changes.length - added - removed} changed`);
	return {
		changes,
		summary: `${changes.length} semantic ${changes.length === 1 ? 'difference' : 'differences'} (${parts.join(', ')})`
	};
}
