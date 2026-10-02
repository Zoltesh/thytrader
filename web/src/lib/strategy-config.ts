/**
 * Exact-rules strategy configuration summary from the snapshot API.
 *
 * The deployment detail page fetches `GET /api/v1/strategies/snapshots/{fingerprint}`
 * and renders the rules this deployment actually runs (its snapshot) — never
 * the strategy's current edit and never a locally reinvented interpretation of
 * the fingerprint. Every derivation here is a pure function over the API
 * payload so the page and unit tests share one reading.
 */
import type { IndicatorDraft, StrategyDefinition, StrategySnapshot } from './strategies';
import { fetchStrategySnapshot, toBuilderModel } from './strategies';
import { conditionToText, plainEnglishSummary } from './strategy-insight';

export type StrategyConfigSummary = {
	/** One-sentence rule reading from the immutable definition. */
	rule: string;
	identity: { label: string; value: string }[];
	indicators: string[];
	entry: string;
	htfEntry: string | null;
	exits: string[];
	sizing: string[];
};

/** Response codes the source route returns for a missing publication. */
export type StrategySourceState =
	| {
			kind: 'loaded';
			fingerprint: string;
			/** Name recorded in the snapshot the deployment runs. */
			name: string;
			/** Owning strategy, or null when the strategy was deleted. */
			strategyId: string | null;
			snapshot: StrategySnapshot;
			summary: StrategyConfigSummary;
	  }
	| { kind: 'unavailable'; fingerprint: string; reason: string };

function indicatorText(indicator: IndicatorDraft): string {
	const input =
		typeof indicator.input === 'string'
			? indicator.input
			: Array.isArray(indicator.input)
				? indicator.input.join('/')
				: 'close';
	const params = Object.entries(indicator.parameters)
		.filter(([, value]) => value !== undefined)
		.map(([key, value]) => `${key}=${String(value)}`)
		.join(', ');
	const clock =
		indicator.timeframe !== undefined && indicator.timeframe !== ''
			? ` ${indicator.timeframe}`
			: '';
	const parameterText = params === '' ? '' : `(${params})`;
	return `${indicator.id}: ${indicator.kind}${parameterText} on ${input}${clock}`;
}

/** Project the immutable definition into display rows for the detail page. */
export function summarizeStrategySource(
	fingerprint: string,
	draft: StrategyDefinition
): StrategyConfigSummary {
	const model = toBuilderModel(draft, 0);
	const identity = [
		{ label: 'Name', value: model.name },
		{ label: 'Market', value: `${model.product_id} · ${model.timeframe}` },
		{ label: 'Side', value: model.side },
		{ label: 'Warmup', value: `${model.warmup_bars} bars` },
		{
			label: 'Entry preference',
			value: String(model.execution?.entry_preference ?? 'unknown')
		},
		{ label: 'Cooldown', value: `${model.cooldown_bars} bars` }
	];
	const exits = [
		`Initial stop: ${model.exits.initial_stop.kind} ${model.exits.initial_stop.multiple}× ${model.exits.initial_stop.atr_indicator}`,
		model.exits.take_profit.kind === 'reward_risk'
			? `Take profit: reward_risk ${model.exits.take_profit.multiple}× risk`
			: 'Take profit: none (stop, trail, or time exit)',
		model.exits.trailing_stop.enabled
			? `Trailing stop: ${model.exits.trailing_stop.multiple}× ${model.exits.trailing_stop.atr_indicator}`
			: 'Trailing stop: off',
		`Time exit: after ${model.exits.time_exit.max_bars_held} bars`
	];
	const sizing = [
		`Risk ${model.sizing.risk_fraction} of equity per trade, ${model.sizing.min_quote_notional}–${model.sizing.max_quote_notional} quote notional`,
		`Max strategy exposure ${model.portfolio_limits.max_strategy_exposure_fraction} · max concurrent positions ${model.portfolio_limits.max_concurrent_positions}`
	];
	return {
		rule: plainEnglishSummary(model),
		identity,
		indicators: model.indicators.map((indicator) => indicatorText(indicator)),
		entry: conditionToText(model.entry.when),
		htfEntry:
			model.htf_filter === null
				? null
				: `${model.htf_filter.timeframe}: ${conditionToText(model.htf_filter.when)} (warmup ${model.htf_filter.warmup_bars} bars)`,
		exits,
		sizing
	};
}

/**
 * Load the exact-rules configuration, failing to an explicit unavailable state.
 *
 * A 404 or transport error never falls back to the current edit: the detail page
 * renders `unavailable` with the reason and keeps the fingerprint as the only
 * identity shown.
 */
export async function loadStrategyConfig(fingerprint: string): Promise<StrategySourceState> {
	try {
		const body = await fetchStrategySnapshot(fingerprint);
		return {
			kind: 'loaded',
			fingerprint,
			name: body.strategy.name,
			strategyId: body.strategy_id,
			snapshot: body,
			summary: summarizeStrategySource(fingerprint, body.strategy)
		};
	} catch (caught) {
		return {
			kind: 'unavailable',
			fingerprint,
			reason: caught instanceof Error ? caught.message : 'Strategy snapshot is unavailable.'
		};
	}
}
