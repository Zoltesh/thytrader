/**
 * Client-side validation of a whole strategy definition: identity, indicators
 * (catalog shape, clocks, warmup), reference instruments, entry / exit / HTF rule
 * trees, sizing, portfolio limits and exits. One line per distinct problem; the
 * server validates everything again. Re-exported by `strategy-insight.ts`.
 */
import {
	MAX_INDICATOR_OFFSET,
	findCatalogEntry,
	indicatorWarmupBars,
	inputMatchesKind,
	isValidOffset,
	parameterProblems
} from './indicator-catalog';
import {
	DECIMAL_PATTERN,
	conditionOperandOffset,
	maximumOperandOffset,
	validateCondition,
	type IndicatorLike
} from './strategy-insight-conditions';
import { isFuturesProductId } from './product-id';
import { validateDerivatives } from './strategies-derivatives';
import { validateReferenceInstruments } from './strategy-insight-references';
import {
	takeProfitMultiple,
	resolvedIndicatorTimeframe,
	validHtfTimeframes,
	type BuilderModel,
	type ConditionDraft,
	type HtfFilterDraft,
	type IndicatorDraft
} from './strategies';

const INDICATOR_ID_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;

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
	const conditions = [model.entry.when];
	if (model.exits.signal_exit !== undefined) conditions.push(model.exits.signal_exit.when);
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
		const sourced = indicator.source !== undefined && indicator.source !== '';
		if (clock === model.timeframe && !sourced) {
			warmupNeeded.set(
				indicator.id,
				indicatorWarmupBars(indicator) + maximumOperandOffset(conditions, indicator.id)
			);
		}
	}
	problems.push(...validateReferenceInstruments(model));
	problems.push(
		...validateDerivatives(
			model.instrument_kind,
			model.derivatives,
			isFuturesProductId(model.product_id)
		)
	);
	problems.push(...validateCondition(model.entry.when, model.indicators, 'Entry'));
	if (model.exits.signal_exit !== undefined) {
		problems.push(
			...validateCondition(model.exits.signal_exit.when, model.indicators, 'Exit rule')
		);
	}
	if (model.htf_filter !== null) {
		problems.push(
			...validateHtfFilter(model.htf_filter, ids, model.timeframe, model.indicators, conditions)
		);
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
	const rewardMultiple = takeProfitMultiple(model.exits.take_profit);
	if (rewardMultiple !== null) {
		validateMultiple(rewardMultiple, 'Take profit reward/risk multiple', 0.5, 10, problems);
	}
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
				(indicator.source === undefined || indicator.source === '') &&
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
	// One line per distinct problem: repeats (e.g. several empty groups) add nothing.
	return [...new Set(problems)];
}

function validateHtfFilter(
	filter: HtfFilterDraft,
	decisionIds: Set<string>,
	decisionTimeframe: string,
	decisionIndicators: IndicatorDraft[],
	decisionConditions: ConditionDraft[]
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
		warmupNeeded.set(
			indicator.id,
			indicatorWarmupBars(indicator) + conditionOperandOffset(filter.when, indicator.id)
		);
	}
	problems.push(...validateCondition(filter.when, filter.indicators, 'HTF filter'));
	const extraOnHtf = decisionIndicators.filter(
		(indicator) => resolvedIndicatorTimeframe(indicator, decisionTimeframe) === filter.timeframe
	);
	for (const indicator of extraOnHtf) {
		warmupNeeded.set(
			`ltf:${indicator.id}`,
			indicatorWarmupBars(indicator) + maximumOperandOffset(decisionConditions, indicator.id)
		);
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
