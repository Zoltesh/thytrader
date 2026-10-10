/**
 * The strategy builder model: its draft types, defaults for optional blocks, and
 * conversion to and from a strategy definition. Re-exported by `strategies.ts`.
 */
import type { StrategyDefinition, StrategyRecord } from './strategies-types';
import {
	type IndicatorDraft,
	type ReferenceInstrumentDraft,
	serializeIndicator
} from './strategies-indicators';
import {
	type ComparisonOperatorValue,
	type ConditionDraft,
	defaultIndicatorOperand
} from './strategies-operands';
import {
	type DerivativesDraft,
	FUTURES_QUOTE_CURRENCY,
	type InstrumentKind,
	instrumentKindOf,
	serializeDerivatives,
	toDerivativesDraft
} from './strategies-derivatives';
import { validHtfTimeframes, validReferenceTimeframes } from './strategies-timeframes';

export type HtfFilterDraft = {
	timeframe: string;
	warmup_bars: number;
	indicators: IndicatorDraft[];
	when: ConditionDraft;
};

export type CoveredInstrumentDraft = {
	product_id: string;
	base_currency: string;
	quote_currency?: string;
};

export type PyramidingDraft = {
	enabled: true;
	require_unrealized_profit: true;
};

export type BuilderModel = {
	strategy_id: string;
	revision: number;
	name: string;
	description: string;
	created_at: string;
	product_id: string;
	base_currency: string;
	/** `instrument.kind` (ADR 0128): `spot` omits the key; `future` trades one CFM contract. */
	instrument_kind: InstrumentKind;
	/** The futures `derivatives` block; null for spot (the document then omits it). */
	derivatives: DerivativesDraft | null;
	additional_instruments: CoveredInstrumentDraft[];
	timeframe: string;
	warmup_bars: number;
	/** Read-only reference series (`data_requirements.reference_instruments`, ADR 0096). */
	reference_instruments: ReferenceInstrumentDraft[];
	indicators: IndicatorDraft[];
	htf_filter: HtfFilterDraft | null;
	entry: { when: ConditionDraft };
	side: 'long' | 'short';
	sizing: { risk_fraction: string; min_quote_notional: string; max_quote_notional: string };
	portfolio_limits: { max_strategy_exposure_fraction: string; max_concurrent_positions: number };
	exits: {
		initial_stop: { kind: string; atr_indicator: string; multiple: string };
		take_profit: TakeProfitDraft;
		trailing_stop:
			| { enabled: false }
			| { enabled: true; kind: 'atr_multiple'; atr_indicator: string; multiple: string };
		time_exit: { max_bars_held: number };
		/** Optional exit rule (ADR 0093); omitted from the document when absent. */
		signal_exit?: SignalExitDraft;
	};
	execution: {
		entry_preference: string;
		max_entry_wait_bars: number;
		on_unfilled_entry: string;
	};
	cooldown_bars: number;
	max_open_positions: number;
	pyramiding: PyramidingDraft | null;
	economic_guard?: { minimum_net_target_return_fraction: string } | null;
	metadata: { tags: string[]; notes: string[] };
};

/**
 * `exits.signal_exit` (ADR 0093): exit an open position when this rule matches on a
 * closed bar after the fill bar. Same grammar and indicator operands as `entry.when`.
 */
export type SignalExitDraft = { when: ConditionDraft };

const MIRRORED_CROSS: Partial<Record<ComparisonOperatorValue, ComparisonOperatorValue>> = {
	crosses_above: 'crosses_below',
	crosses_below: 'crosses_above'
};

/**
 * Starting rule for a new "Exit when" block: the mirror of the entry's first top-level
 * crossover (`fast crosses above slow` becomes `fast crosses below slow`, so a trend is
 * held until it reverses), else one comparison on the first indicator for the author to edit.
 */
export function defaultSignalExit(
	entry: ConditionDraft,
	indicators: IndicatorDraft[]
): SignalExitDraft {
	const children = 'all' in entry ? entry.all : 'any' in entry ? entry.any : [];
	for (const child of children) {
		if (!('operator' in child)) continue;
		const operator = MIRRORED_CROSS[child.operator];
		if (operator !== undefined) {
			return {
				when: { all: [{ left: { ...child.left }, operator, right: { ...child.right } }] }
			};
		}
	}
	return {
		when: {
			all: [
				{
					left: defaultIndicatorOperand(indicators),
					operator: 'less_than',
					right: { literal: '0' }
				}
			]
		}
	};
}

/** Reward/risk take-profit, or `none`: exit on the stop, ATR trail, or time exit (ADR 0090). */
export type TakeProfitDraft = { kind: 'reward_risk'; multiple: string } | { kind: 'none' };

/** The reward/risk multiple, or null when the strategy declares no take-profit. */
export function takeProfitMultiple(takeProfit: TakeProfitDraft): string | null {
	return takeProfit.kind === 'reward_risk' ? takeProfit.multiple : null;
}

/** One phrase for summaries: `take profit at 2× risk` or `no take-profit`. */
export function takeProfitPhrase(takeProfit: TakeProfitDraft): string {
	const multiple = takeProfitMultiple(takeProfit);
	return multiple === null ? 'no take-profit' : `take profit at ${multiple}× risk`;
}

/** Quote currency for labels: `USDC` for `BTC-USDC`, or `quote` while the id is incomplete. */
export function quoteLabelFor(productId: string): string {
	return quoteCurrencyFor(productId.trim().toUpperCase(), 'quote');
}

/**
 * Quote label of the strategy the form edits: `USD` for a futures contract (CFM
 * contracts settle in USD), else the spot product id's quote via `quoteLabelFor`.
 */
export function builderQuoteLabel(
	model: Pick<BuilderModel, 'product_id' | 'instrument_kind'>
): string {
	return model.instrument_kind === 'future'
		? FUTURES_QUOTE_CURRENCY
		: quoteLabelFor(model.product_id);
}

/** A new reference: BTC in the strategy's quote on 1d (or the coarsest legal clock). */
export function defaultReferenceInstrument(model: BuilderModel): ReferenceInstrumentDraft {
	const taken = new Set(model.reference_instruments.map((reference) => reference.id));
	let id = 'btc';
	for (let index = 2; taken.has(id); index += 1) id = `ref${index}`;
	const clocks = validReferenceTimeframes(model.timeframe);
	const timeframe = clocks.includes('1d') ? '1d' : (clocks.at(-1) ?? model.timeframe);
	return { id, product_id: `BTC-${quoteCurrencyFor(model.product_id, 'USDC')}`, timeframe };
}

/**
 * Seed a conservative HTF trend filter when the operator enables the optional block.
 */
export function defaultHtfFilter(decisionTimeframe: string): HtfFilterDraft {
	const timeframes = validHtfTimeframes(decisionTimeframe);
	const timeframe = timeframes.includes('1h') ? '1h' : (timeframes[0] ?? '6h');
	return {
		timeframe,
		warmup_bars: 50,
		indicators: [
			{ id: 'htf_ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
			{ id: 'htf_ema_slow', kind: 'ema', input: 'close', parameters: { period: 50 } }
		],
		when: {
			all: [
				{
					left: { indicator: 'htf_ema_fast' },
					operator: 'greater_than',
					right: { indicator: 'htf_ema_slow' }
				}
			]
		}
	};
}

function toHtfFilterDraft(raw: unknown): HtfFilterDraft | null {
	if (raw === null || raw === undefined || typeof raw !== 'object') return null;
	const filter = raw as {
		timeframe?: string;
		data_requirements?: { warmup_bars?: number };
		indicators?: IndicatorDraft[];
		when?: ConditionDraft;
	};
	if (filter.timeframe === undefined || filter.when === undefined) return null;
	return {
		timeframe: filter.timeframe,
		warmup_bars: filter.data_requirements?.warmup_bars ?? 50,
		indicators: filter.indicators ?? [],
		when: filter.when
	};
}

export function toBuilderModel(strategy: StrategyDefinition, revision: number): BuilderModel {
	const entry = strategy.entry as {
		when: ConditionDraft;
		cooldown_bars: number;
		side?: 'long' | 'short';
		max_open_positions?: number;
		pyramiding?: PyramidingDraft | null;
		economic_guard?: { minimum_net_target_return_fraction: string } | null;
	};
	const extras = (strategy.additional_instruments as CoveredInstrumentDraft[] | undefined) ?? [];
	const exits = strategy.exits as BuilderModel['exits'];
	return {
		strategy_id: strategy.strategy_id,
		revision,
		name: strategy.name,
		description: strategy.description ?? '',
		created_at: strategy.created_at,
		product_id: (strategy.instrument as { product_id: string }).product_id,
		base_currency: (strategy.instrument as { base_currency: string }).base_currency,
		instrument_kind: instrumentKindOf(strategy.instrument),
		derivatives: toDerivativesDraft(strategy.derivatives),
		additional_instruments: extras.map((item) => ({
			product_id: item.product_id,
			base_currency: item.base_currency,
			quote_currency: quoteCurrencyFor(item.product_id, item.quote_currency ?? 'USD')
		})),
		timeframe: strategy.timeframe as string,
		warmup_bars: (strategy.data_requirements as { warmup_bars: number }).warmup_bars,
		reference_instruments: (
			(strategy.data_requirements as { reference_instruments?: ReferenceInstrumentDraft[] })
				.reference_instruments ?? []
		).map((reference) => ({ ...reference })),
		indicators: ((strategy.indicators as IndicatorDraft[]) ?? []).map((indicator) => ({
			...indicator,
			timeframe: indicator.timeframe ?? '',
			source: indicator.source ?? ''
		})),
		htf_filter: toHtfFilterDraft(strategy.htf_filter),
		entry: { when: entry.when },
		side: entry.side === 'short' ? 'short' : 'long',
		sizing: {
			risk_fraction: strategy.sizing.risk_fraction,
			min_quote_notional: strategy.sizing.min_quote_notional,
			max_quote_notional: strategy.sizing.max_quote_notional
		},
		portfolio_limits: {
			max_strategy_exposure_fraction: strategy.portfolio_limits.max_strategy_exposure_fraction,
			max_concurrent_positions: strategy.portfolio_limits.max_concurrent_positions ?? 1
		},
		exits,
		execution: strategy.execution as BuilderModel['execution'],
		cooldown_bars: entry.cooldown_bars,
		max_open_positions: entry.max_open_positions ?? 1,
		pyramiding: entry.pyramiding ?? null,
		economic_guard: entry.economic_guard ?? null,
		metadata: strategy.metadata as BuilderModel['metadata']
	};
}

/**
 * Quote currency of a Coinbase spot product id (`BASE-QUOTE`). The backend requires
 * `product_id == base-quote`, so the quote is always derived from the id; `fallback`
 * applies only when the id does not have that shape.
 */
export function quoteCurrencyFor(productId: string, fallback = 'USD'): string {
	const parts = productId.split('-');
	return parts.length === 2 && parts[1].length > 0 ? parts[1] : fallback;
}

export function fromBuilderModel(model: BuilderModel): StrategyDefinition {
	return {
		schema_version: '1.0',
		strategy_id: model.strategy_id,
		name: model.name,
		description: model.description.trim().length > 0 ? model.description : null,
		created_at: model.created_at,
		instrument:
			model.instrument_kind === 'future'
				? {
						product_id: model.product_id,
						base_currency: model.base_currency,
						quote_currency: FUTURES_QUOTE_CURRENCY,
						kind: 'future'
					}
				: {
						product_id: model.product_id,
						base_currency: model.base_currency,
						quote_currency: quoteCurrencyFor(model.product_id)
					},
		...(model.additional_instruments.length === 0
			? {}
			: {
					additional_instruments: model.additional_instruments.map((item) => ({
						product_id: item.product_id,
						base_currency: item.base_currency,
						quote_currency: quoteCurrencyFor(item.product_id, item.quote_currency ?? 'USD')
					}))
				}),
		timeframe: model.timeframe,
		data_requirements: {
			warmup_bars: model.warmup_bars,
			required_fields: ['open', 'high', 'low', 'close', 'volume'],
			...(model.reference_instruments.length === 0
				? {}
				: {
						reference_instruments: model.reference_instruments.map((reference) => ({
							id: reference.id,
							product_id: reference.product_id.trim().toUpperCase(),
							timeframe: reference.timeframe
						}))
					})
		},
		indicators: model.indicators.map((indicator) => serializeIndicator(indicator, model.timeframe)),
		...(model.htf_filter === null
			? {}
			: {
					htf_filter: {
						timeframe: model.htf_filter.timeframe,
						data_requirements: {
							warmup_bars: model.htf_filter.warmup_bars,
							required_fields: ['open', 'high', 'low', 'close', 'volume']
						},
						indicators: model.htf_filter.indicators.map((indicator) =>
							serializeIndicator(indicator)
						),
						when: model.htf_filter.when
					}
				}),
		entry: {
			side: model.side,
			when: model.entry.when,
			cooldown_bars: model.cooldown_bars,
			max_open_positions: model.max_open_positions,
			...(model.pyramiding === null ? {} : { pyramiding: model.pyramiding }),
			...(model.economic_guard == null ? {} : { economic_guard: model.economic_guard })
		},
		sizing: { kind: 'risk_fraction', ...model.sizing },
		portfolio_limits: {
			max_strategy_exposure_fraction: model.portfolio_limits.max_strategy_exposure_fraction,
			max_concurrent_positions: model.portfolio_limits.max_concurrent_positions
		},
		exits: model.exits,
		execution: model.execution,
		metadata: model.metadata,
		...(model.instrument_kind !== 'future' || model.derivatives === null
			? {}
			: { derivatives: serializeDerivatives(model.derivatives) })
	};
}

/**
 * Builder model for a saved document, or null when the document cannot be
 * projected into the form (an invalid work in progress that is missing blocks).
 */
export function builderModelFromRecord(record: StrategyRecord): BuilderModel | null {
	const source = (record.strategy ?? record.document) as StrategyDefinition;
	try {
		const model = toBuilderModel(source, record.revision);
		if (typeof model.name !== 'string' || typeof model.product_id !== 'string') return null;
		return model;
	} catch {
		return null;
	}
}
