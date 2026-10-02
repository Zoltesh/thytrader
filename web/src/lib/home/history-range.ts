/**
 * Home chart ranges (1D · 1W · 1M · 3M) over the existing portfolio-history API.
 *
 * `GET /api/v1/portfolio/history` serves `24h`, `7d`, `30d`, and `all`. 3M has
 * no server range, so it reads `all` and keeps only the snapshots from the
 * last 90 days. Nothing is interpolated or invented.
 */
import type { HistoryEntry, HistoryRange } from '$lib/portfolio';

export type HomeChartRange = '1D' | '1W' | '1M' | '3M';

export type HomeChartRangeOption = {
	id: HomeChartRange;
	/** The server range this pill reads. */
	apiRange: HistoryRange;
	/** Client-side window in days when the server range is wider than the pill. */
	clipDays: number | null;
	/** Plain words for notes and screen readers. */
	description: string;
};

export const HOME_CHART_RANGES: readonly HomeChartRangeOption[] = [
	{ id: '1D', apiRange: '24h', clipDays: null, description: 'last 24 hours' },
	{ id: '1W', apiRange: '7d', clipDays: null, description: 'last 7 days' },
	{ id: '1M', apiRange: '30d', clipDays: null, description: 'last 30 days' },
	{ id: '3M', apiRange: 'all', clipDays: 90, description: 'last 90 days' }
];

export const DEFAULT_HOME_CHART_RANGE: HomeChartRange = '1W';

const DAY_MS = 86_400_000;

export function homeChartRangeOption(id: HomeChartRange): HomeChartRangeOption {
	const option = HOME_CHART_RANGES.find((candidate) => candidate.id === id);
	if (option === undefined) throw new Error(`Unknown chart range: ${id}`);
	return option;
}

/**
 * Newest-first entries inside the pill's window.
 *
 * Only 3M clips: it reads `all` and keeps snapshots taken at or after
 * `now - 90 days`. Entries with an unreadable timestamp are dropped rather
 * than placed somewhere they may not belong.
 */
export function clipHistoryToRange(
	entries: readonly HistoryEntry[],
	option: HomeChartRangeOption,
	nowMs: number
): HistoryEntry[] {
	if (option.clipDays === null) return [...entries];
	const floor = nowMs - option.clipDays * DAY_MS;
	return entries.filter((entry) => {
		const time = Date.parse(entry.as_of);
		return Number.isFinite(time) && time >= floor;
	});
}

/**
 * Most entries one history response carries (`_MAX_HISTORY_ENTRIES` in
 * `src/thytrader/api/routes/portfolio_history.py`). A response at the cap was
 * thinned by the API to evenly bucketed representative snapshots.
 */
export const HISTORY_RESPONSE_CAP = 300;

/** Whether the API thinned this response to representative snapshots. */
export function isThinnedHistory(responseCount: number): boolean {
	return responseCount >= HISTORY_RESPONSE_CAP;
}

/**
 * Expected seconds between neighbouring samples, used to decide what is a gap.
 *
 * Below the cap the API returned every snapshot, so the configured cadence is
 * the expectation (unchanged behaviour). At the cap it returned one snapshot
 * per bucket, so neighbours sit about one bucket apart: the median spacing is
 * the expectation, never less than the cadence. A real outage still shows as a
 * gap because it stretches one spacing far past the median; only holes shorter
 * than one bucket are invisible at that resolution.
 */
export function expectedSampleSpacingSeconds(
	entries: readonly HistoryEntry[],
	samplingIntervalSeconds: number,
	responseCount: number
): number {
	const cadence = Math.max(1, samplingIntervalSeconds);
	if (!isThinnedHistory(responseCount)) return cadence;
	const times = entries
		.map((entry) => Date.parse(entry.as_of))
		.filter((time) => Number.isFinite(time))
		.sort((left, right) => left - right);
	const spacings: number[] = [];
	for (let index = 1; index < times.length; index += 1) {
		const spacing = (times[index] - times[index - 1]) / 1000;
		if (spacing > 0) spacings.push(spacing);
	}
	if (spacings.length === 0) return cadence;
	spacings.sort((left, right) => left - right);
	const middle = Math.floor(spacings.length / 2);
	const median =
		spacings.length % 2 === 1 ? spacings[middle] : (spacings[middle - 1] + spacings[middle]) / 2;
	return Math.max(cadence, median);
}
