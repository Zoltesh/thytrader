/**
 * Plain-language strategy text: a rule tree as one line, the definition summary,
 * the reference-gate and signal-exit sentences, and the data a strategy needs
 * before its first signal. Re-exported by `strategy-insight.ts`.
 */
import { indicatorWarmupBars } from './indicator-catalog';
import { isComparison, isGroup } from './strategy-insight-conditions';
import {
	builderQuoteLabel,
	extraIndicatorTimeframes,
	takeProfitPhrase,
	type BuilderModel,
	type ConditionDraft,
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
	const text =
		operand.series === undefined ? operand.indicator : `${operand.indicator}.${operand.series}`;
	return operand.offset
		? `${text} (${operand.offset} bar${operand.offset === 1 ? '' : 's'} ago)`
		: text;
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
	const quote = builderQuoteLabel(model);
	const entryText = conditionToText(model.entry.when);
	const htf =
		model.htf_filter === null
			? ''
			: ` HTF filter on ${model.htf_filter.timeframe}: ${conditionToText(model.htf_filter.when)}.`;
	return [
		`${model.name}: when ${entryText}, enter long on ${model.product_id} ${model.timeframe}.${htf}`,
		...referenceGateSentence(model),
		`Risk ${model.sizing.risk_fraction} of equity per trade between ${model.sizing.min_quote_notional} ${quote} and ${model.sizing.max_quote_notional} ${quote}.`,
		`Initial stop ${model.exits.initial_stop.multiple}× ATR, ${takeProfitPhrase(model.exits.take_profit)}, time exit after ${model.exits.time_exit.max_bars_held} bars.`,
		...signalExitSentence(model)
	].join(' ');
}

/**
 * Plain-language reference gate (ADR 0096): `Gated on BTC-USDC 1d (btc: btc_close,
 * btc_ema) as a read-only reference instrument; only closed reference bars count, and
 * entries skip while a reference is stale or missing.` Empty without references.
 */
export function referenceGateSentence(model: BuilderModel): string[] {
	if (model.reference_instruments.length === 0) return [];
	const series = model.reference_instruments.map((reference) => {
		const readers = model.indicators
			.filter((indicator) => indicator.source === reference.id)
			.map((indicator) => indicator.id);
		const listed = readers.length === 0 ? 'no indicators yet' : readers.join(', ');
		return `${reference.product_id} ${reference.timeframe} (${reference.id}: ${listed})`;
	});
	return [
		`Gated on ${series.join('; ')} as read-only reference instruments; only closed reference bars count, orders stay on ${model.product_id}, and entries skip while a reference is stale or missing.`
	];
}

/**
 * Plain-language exit rule (ADR 0093): `Exit when fast crosses below slow (sells at that
 * bar's close; the initial stop still guards the position).` Empty without a rule.
 */
export function signalExitSentence(model: BuilderModel): string[] {
	const signalExit = model.exits.signal_exit;
	if (signalExit === undefined) return [];
	return [
		`Exit when ${conditionToText(signalExit.when)} (sells at that bar's close; the initial stop still guards the position).`
	];
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
	for (const reference of model.reference_instruments) {
		const readers = model.indicators.filter((indicator) => indicator.source === reference.id);
		const warmup = Math.max(1, ...readers.map((indicator) => indicatorWarmupBars(indicator)));
		parts.push(
			`Reference ${reference.product_id} ${reference.timeframe} (${reference.id}): ${warmup} completed bars, using only the last reference bar that closed by each ${model.timeframe} close.`
		);
	}
	if (model.htf_filter !== null || extra.length > 0 || model.reference_instruments.length > 0) {
		parts.push('Research, paper, and live share that alignment.');
	}
	return parts.join(' ');
}
