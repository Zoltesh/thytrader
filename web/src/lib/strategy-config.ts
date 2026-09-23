/**
 * Exact-version strategy configuration summary from the canonical source API.
 *
 * The deployment detail page fetches `GET /api/v1/strategies/source/{fingerprint}`
 * and renders the immutable rule/config of the exact published version this
 * deployment runs — never a generic link and never a locally reinvented
 * interpretation of the fingerprint. Every derivation here is a pure function
 * over the API payload so the page and unit tests share one reading.
 */
import type { IndicatorDraft, StrategyDraft } from './strategies';
import { toBuilderModel } from './strategies';
import { conditionToText, plainEnglishSummary } from './strategy-insight';

export type StrategySourceResponse = { strategy: StrategyDraft };

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
	| { kind: 'loaded'; fingerprint: string; summary: StrategyConfigSummary }
	| { kind: 'unavailable'; fingerprint: string; reason: string };

async function fetchStrategySourceResponse(fingerprint: string): Promise<StrategySourceResponse> {
	const response = await fetch(`/api/v1/strategies/source/${encodeURIComponent(fingerprint)}`, {
		headers: { Accept: 'application/json' }
	});
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
		const detail = typeof body.detail === 'string' ? body.detail : undefined;
		throw new Error(detail ?? `Strategy source is unavailable (HTTP ${response.status}).`);
	}
	return (await response.json()) as StrategySourceResponse;
}

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
	draft: StrategyDraft
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
		`Take profit: ${model.exits.take_profit.kind} ${model.exits.take_profit.multiple}× risk`,
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
 * Load the exact-version configuration, failing to an explicit unavailable state.
 *
 * A 404 or transport error never falls back to another version: the detail page
 * renders `unavailable` with the reason and keeps the fingerprint as the only
 * identity shown.
 */
export async function loadStrategyConfig(fingerprint: string): Promise<StrategySourceState> {
	try {
		const body = await fetchStrategySourceResponse(fingerprint);
		return {
			kind: 'loaded',
			fingerprint,
			summary: summarizeStrategySource(fingerprint, body.strategy)
		};
	} catch (caught) {
		return {
			kind: 'unavailable',
			fingerprint,
			reason: caught instanceof Error ? caught.message : 'Strategy source is unavailable.'
		};
	}
}
