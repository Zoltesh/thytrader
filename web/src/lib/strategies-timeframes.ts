/**
 * Execution timeframes (legal clocks, HTF and reference clocks, extra indicator clocks),
 * dataset evaluation windows, and UTC `datetime-local` values. Re-exported by `strategies.ts`.
 */
import type { Dataset } from './strategies-types';
import type { IndicatorDraft } from './strategies-indicators';

/** Duration-ascending legal strategy, paper, live, discretionary, and HTF tokens. */
export const EXECUTION_TIMEFRAMES = [
	'1m',
	'5m',
	'15m',
	'30m',
	'1h',
	'2h',
	'4h',
	'6h',
	'1d'
] as const;

export type ExecutionTimeframe = (typeof EXECUTION_TIMEFRAMES)[number];

const TIMEFRAME_SECONDS: Record<ExecutionTimeframe, number> = {
	'1m': 60,
	'5m': 300,
	'15m': 900,
	'30m': 1_800,
	'1h': 3_600,
	'2h': 7_200,
	'4h': 14_400,
	'6h': 21_600,
	'1d': 86_400
};

/** Reference clocks for one decision clock: the decision clock or a coarser integer multiple. */
export function validReferenceTimeframes(decisionTimeframe: string): string[] {
	if (TIMEFRAME_SECONDS[decisionTimeframe as ExecutionTimeframe] === undefined) return [];
	return [decisionTimeframe, ...validHtfTimeframes(decisionTimeframe)];
}

/**
 * Return HTF clocks that are strictly coarser integer multiples of the LTF decision clock.
 */
export function validHtfTimeframes(decisionTimeframe: string): string[] {
	const decisionSeconds = TIMEFRAME_SECONDS[decisionTimeframe as ExecutionTimeframe];
	if (decisionSeconds === undefined) return [];
	return EXECUTION_TIMEFRAMES.filter((timeframe) => {
		const seconds = TIMEFRAME_SECONDS[timeframe];
		return seconds > decisionSeconds && seconds % decisionSeconds === 0;
	});
}

/**
 * Return the clock one LTF-list indicator evaluates on.
 */
export function resolvedIndicatorTimeframe(
	indicator: IndicatorDraft,
	decisionTimeframe: string
): string {
	if (indicator.timeframe === undefined || indicator.timeframe === '') {
		return decisionTimeframe;
	}
	return indicator.timeframe;
}

/**
 * Return extra LTF-list clocks in venue-duration order.
 */
export function extraIndicatorTimeframes(
	indicators: IndicatorDraft[],
	decisionTimeframe: string
): string[] {
	const clocks = new Set<string>();
	for (const indicator of indicators) {
		const clock = resolvedIndicatorTimeframe(indicator, decisionTimeframe);
		if (clock !== decisionTimeframe) clocks.add(clock);
	}
	return EXECUTION_TIMEFRAMES.filter((timeframe) => clocks.has(timeframe));
}

/**
 * Return extra LTF-list clocks that need their own research dataset fingerprint.
 */
export function unboundIndicatorTimeframes(
	indicators: IndicatorDraft[],
	decisionTimeframe: string,
	htfTimeframe: string | null | undefined
): string[] {
	return extraIndicatorTimeframes(indicators, decisionTimeframe).filter(
		(timeframe) => timeframe !== htfTimeframe
	);
}

/**
 * Collapse verified cumulative revisions to one latest dataset per product and timeframe.
 * Every revision of a product/timeframe shares its start and grows its end, so the
 * newest `ends_at` (tiebroken by earlier `starts_at`) is a strict superset.
 */
export function latestDatasets(datasets: Dataset[]): Dataset[] {
	const byMarket = new Map<string, Dataset>();
	for (const dataset of datasets) {
		const key = `${dataset.product_id}:${dataset.timeframe}`;
		const current = byMarket.get(key);
		if (
			current === undefined ||
			dataset.ends_at > current.ends_at ||
			(dataset.ends_at === current.ends_at && dataset.starts_at < current.starts_at)
		) {
			byMarket.set(key, dataset);
		}
	}
	return [...byMarket.values()];
}

/**
 * Parse one zone-less `datetime-local` input value as the UTC instant it
 * represents. The launch form labels both fields UTC, so local-time
 * interpretation would silently shift the identity-bearing evaluation window.
 */
export function parseUtcInputValue(value: string): Date {
	return new Date(`${value}Z`);
}

/** Format one instant as a zone-less `datetime-local` string in UTC. */
export function formatUtcInputValue(instant: Date): string {
	return instant.toISOString().slice(0, 16);
}

/**
 * Compute the inclusive evaluation window one dataset can support for a warmup.
 * The dataset must supply `warmupBars` completed candles before the window and
 * one candle after it, whose open liquidates any inventory still held at the end.
 */
export function datasetEvaluationWindow(
	dataset: Dataset,
	warmupBars: number,
	timeframe: string = '1h'
): { min: string; max: string } {
	const seconds = TIMEFRAME_SECONDS[timeframe as ExecutionTimeframe] ?? 3_600;
	const barMs = seconds * 1_000;
	return {
		min: formatUtcInputValue(new Date(new Date(dataset.starts_at).getTime() + warmupBars * barMs)),
		max: formatUtcInputValue(new Date(new Date(dataset.ends_at).getTime() - barMs))
	};
}

/**
 * Describe the inclusive UTC evaluation window a dataset can support.
 * Names the strategy timeframe so 5m windows are not labeled as hours.
 */
export function researchWindowHint(
	bounds: { min: string; max: string },
	warmupBars: number,
	timeframe: string
): string {
	const barTimeframe = timeframe.trim() === '' ? '1h' : timeframe;
	return `Usable window for this dataset: ${bounds.min.replace('T', ' ')} → ${bounds.max.replace('T', ' ')} (UTC, ${barTimeframe} bars). It must fit inside the dataset with ${warmupBars} warmup bars before it and one candle after it.`;
}
