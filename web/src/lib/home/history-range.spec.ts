import { describe, expect, it } from 'vitest';
import type { HistoryEntry } from '$lib/portfolio';
import {
	HISTORY_RESPONSE_CAP,
	HOME_CHART_RANGES,
	clipHistoryToRange,
	expectedSampleSpacingSeconds,
	homeChartRangeOption,
	isThinnedHistory
} from './history-range';

const NOW = Date.parse('2026-10-02T12:00:00Z');

function entry(asOf: string, amount = '100'): HistoryEntry {
	return { as_of: asOf, total_value: { amount, currency: 'USD' } };
}

/** Newest-first entries every `stepSeconds`, ending at `end`. */
function series(count: number, stepSeconds: number, end = NOW): HistoryEntry[] {
	return Array.from({ length: count }, (_, index) =>
		entry(new Date(end - index * stepSeconds * 1000).toISOString())
	);
}

describe('chart ranges', () => {
	it('maps the pills onto the API ranges, 3M onto `all` with a 90-day clip', () => {
		expect(
			HOME_CHART_RANGES.map((option) => [option.id, option.apiRange, option.clipDays])
		).toEqual([
			['1D', '24h', null],
			['1W', '7d', null],
			['1M', '30d', null],
			['3M', 'all', 90]
		]);
		expect(homeChartRangeOption('3M').description).toBe('last 90 days');
	});

	it('clips only 3M and drops unreadable timestamps there', () => {
		const entries = [
			entry('2026-10-01T00:00:00Z'),
			entry('2026-07-05T00:00:00Z'),
			entry('2026-07-03T00:00:00Z'),
			entry('not a time')
		];
		expect(clipHistoryToRange(entries, homeChartRangeOption('1M'), NOW)).toHaveLength(4);
		expect(
			clipHistoryToRange(entries, homeChartRangeOption('3M'), NOW).map((e) => e.as_of)
		).toEqual(['2026-10-01T00:00:00Z', '2026-07-05T00:00:00Z']);
	});
});

describe('expectedSampleSpacingSeconds', () => {
	it('keeps the configured cadence when the API returned every snapshot', () => {
		const sparse = series(3, 3600);
		expect(isThinnedHistory(sparse.length)).toBe(false);
		expect(expectedSampleSpacingSeconds(sparse, 300, sparse.length)).toBe(300);
	});

	it('uses the median bucket spacing for a thinned response, so an outage still stands out', () => {
		const thinned = series(HISTORY_RESPONSE_CAP, 2016);
		// One six-hour worker outage between two representative samples.
		const withOutage = [
			...thinned.slice(0, 150),
			...thinned
				.slice(150)
				.map((item) => entry(new Date(Date.parse(item.as_of) - 6 * 3600 * 1000).toISOString()))
		];
		expect(isThinnedHistory(withOutage.length)).toBe(true);
		const spacing = expectedSampleSpacingSeconds(withOutage, 300, withOutage.length);
		expect(spacing).toBe(2016);
		expect(6 * 3600 + 2016).toBeGreaterThan(2 * spacing);
	});

	it('never expects less than the cadence', () => {
		const dense = series(HISTORY_RESPONSE_CAP, 60);
		expect(expectedSampleSpacingSeconds(dense, 300, dense.length)).toBe(300);
	});
});
