import { describe, expect, it } from 'vitest';
import {
	chartHasGaps,
	formatUsd,
	isHistoryStale,
	isHonestLineValuePoint,
	MAX_PORTFOLIO_CHART_WHITESPACE,
	permissionLabel,
	portfolioChange,
	portfolioHistoryChartModel,
	type HistoryEntry
} from './portfolio';

describe('portfolio presentation', () => {
	it('formats exact decimal strings as USD', () => {
		expect(formatUsd('98542.17')).toBe('$98,542.17');
	});

	it('preserves precise decimal strings while rounding only to display cents', () => {
		expect(formatUsd('9007199254740992.01')).toBe('$9,007,199,254,740,992.01');
		expect(formatUsd('0.005')).toBe('$0.01');
	});

	it('formats detected permission labels for display', () => {
		expect(permissionLabel('transfer')).toBe('Transfer');
	});
});

describe('portfolioHistoryChartModel', () => {
	function entry(amount: string, asOf: string): HistoryEntry {
		return { as_of: asOf, total_value: { amount, currency: 'USD' } };
	}

	function valuedRunLengths(
		series: ReturnType<typeof portfolioHistoryChartModel>['series']
	): number[] {
		const runs: number[] = [];
		let length = 0;
		for (const point of series) {
			if (isHonestLineValuePoint(point)) {
				length += 1;
				continue;
			}
			if (length > 0) {
				runs.push(length);
				length = 0;
			}
		}
		if (length > 0) runs.push(length);
		return runs;
	}

	it('returns empty result for fewer than 2 entries', () => {
		const result = portfolioHistoryChartModel([entry('100', '2026-07-27T10:00:00Z')]);
		expect(result.series).toEqual([]);
		expect(result.samples).toEqual([]);
		expect(result.hasGaps).toBe(false);
	});

	it('computes finite values for a normal dataset', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('200', '2026-07-27T11:00:00Z'),
			entry('150', '2026-07-27T12:00:00Z')
		];
		const result = portfolioHistoryChartModel(entries, 3600);
		expect(result.samples.map((sample) => sample.value)).toEqual([100, 200, 150]);
		expect(result.minAmount).toBe('100');
		expect(result.maxAmount).toBe('200');
		expect(result.series).toHaveLength(3);
		expect(result.hasGaps).toBe(false);
	});

	it('handles flat line where all values are identical', () => {
		const entries = [entry('500', '2026-07-27T10:00:00Z'), entry('500', '2026-07-27T11:00:00Z')];
		const result = portfolioHistoryChartModel(entries, 3600);
		expect(result.minAmount).toBe('500');
		expect(result.maxAmount).toBe('500');
		expect(result.series).toHaveLength(2);
		expect(
			result.series.every((point) => isHonestLineValuePoint(point) && Number.isFinite(point.value))
		).toBe(true);
	});

	it('handles exact decimal strings with high precision', () => {
		const entries = [
			entry('12345.678901', '2026-07-27T10:00:00Z'),
			entry('98765.432109', '2026-07-27T11:00:00Z')
		];
		const result = portfolioHistoryChartModel(entries);
		expect(result.samples[0]?.value).toBeCloseTo(12345.678901);
		expect(result.samples[1]?.value).toBeCloseTo(98765.432109);
		expect(result.minAmount).toBe('12345.678901');
		expect(result.maxAmount).toBe('98765.432109');
		expect(result.samples[0]?.amount).toBe('12345.678901');
	});

	it('keeps huge and tiny exact amounts finite in chart geometry', () => {
		const tiny = `0.${'0'.repeat(500)}1`;
		const huge = `1${'0'.repeat(500)}`;
		const result = portfolioHistoryChartModel(
			[entry(tiny, '2026-07-27T10:00:00Z'), entry(huge, '2026-07-27T11:00:00Z')],
			3600
		);

		expect(result.samples.every((sample) => Number.isFinite(sample.value))).toBe(true);
		expect(result.minAmount).toBe(tiny);
		expect(result.maxAmount).toBe(huge);
		const first = result.samples[0];
		const last = result.samples[1];
		expect(first?.value).toBeLessThan(last?.value ?? Number.POSITIVE_INFINITY);
	});

	it('splits visual segments when snapshots have a worker-downtime gap', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T10:05:00Z'),
			entry('120', '2026-07-27T11:00:00Z'),
			entry('130', '2026-07-27T11:05:00Z')
		];

		const data = portfolioHistoryChartModel(entries, 300);

		expect(valuedRunLengths(data.series)).toEqual([2, 2]);
		expect(data.hasGaps).toBe(true);
		expect(chartHasGaps(data.samples)).toBe(true);
		expect(data.whitespaceCount).toBeGreaterThan(0);
	});

	it('places X by wall-clock time so a long gap occupies more space than nearby samples', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T11:00:00Z'),
			entry('120', '2026-07-27T14:00:00Z')
		];
		const result = portfolioHistoryChartModel(entries, 3600);
		const firstTime = result.samples[0]?.time ?? 0;
		const middleTime = result.samples[1]?.time ?? 0;
		const lastTime = result.samples[2]?.time ?? 0;
		const middleIndex = result.series.findIndex((point) => point.time === middleTime);
		const lastIndex = result.series.length - 1;

		expect(result.series[0]?.time).toBe(firstTime);
		expect(result.series[lastIndex]?.time).toBe(lastTime);
		expect(middleIndex / lastIndex).toBeCloseTo(0.25);
		expect(middleIndex).toBeLessThan(lastIndex / 2);
		expect(result.whitespaceCount).toBeGreaterThan(0);
	});

	it('keeps equally spaced snapshots equally spaced on X', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T11:00:00Z'),
			entry('120', '2026-07-27T12:00:00Z')
		];
		const result = portfolioHistoryChartModel(entries, 3600);
		const times = result.series.map((point) => point.time);

		expect(times[1]! - times[0]!).toBe(times[2]! - times[1]!);
		expect(result.hasGaps).toBe(false);
		expect(result.whitespaceCount).toBe(0);
	});

	it('keeps an orphan post-gap snapshot as a valued point, without Y interpolation, and reports the gap', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T10:05:00Z'),
			entry('120', '2026-07-27T11:00:00Z')
		];
		const data = portfolioHistoryChartModel(entries, 300);
		const firstTime = data.samples[0]?.time ?? 0;
		const middleTime = data.samples[1]?.time ?? 0;
		const lastTime = data.samples[2]?.time ?? 0;
		const middleIndex = data.series.findIndex((point) => point.time === middleTime);
		const lastIndex = data.series.length - 1;

		expect(data.samples).toHaveLength(3);
		expect(data.samples[2]?.gapBefore).toBe(true);
		expect(data.hasGaps).toBe(true);
		expect(chartHasGaps(data.samples)).toBe(true);
		expect(valuedRunLengths(data.series)).toEqual([2, 1]);
		expect(data.whitespaceCount).toBeGreaterThan(0);
		expect(middleIndex / lastIndex).toBeCloseTo(5 / 60);
		expect(data.series[lastIndex]?.time).toBe(lastTime);
		expect(data.series[0]?.time).toBe(firstTime);
		expect(data.samples[2]?.value).not.toBe(data.samples[1]?.value);
		const afterMiddle = data.series[middleIndex + 1];
		expect(afterMiddle && !isHonestLineValuePoint(afterMiddle)).toBe(true);
	});

	it('caps whitespace for a multi-year hole while still breaking the line', () => {
		const entries = [entry('100', '2020-01-01T00:00:00Z'), entry('110', '2024-01-01T00:00:00Z')];
		const data = portfolioHistoryChartModel(entries, 300);

		expect(data.hasGaps).toBe(true);
		expect(data.whitespaceCount).toBeGreaterThan(0);
		expect(data.whitespaceCount).toBeLessThanOrEqual(MAX_PORTFOLIO_CHART_WHITESPACE);
		expect(valuedRunLengths(data.series)).toEqual([1, 1]);
	});
});

describe('history presentation', () => {
	function entry(amount: string, asOf: string): HistoryEntry {
		return { as_of: asOf, total_value: { amount, currency: 'USD' } };
	}

	it('calculates the gain and percent against the oldest selected observation', () => {
		const change = portfolioChange([
			entry('125', '2026-07-27T12:00:00Z'),
			entry('100', '2026-07-27T10:00:00Z')
		]);

		expect(change).toEqual({ amount: '25', percent: '25.00', direction: 'gain' });
	});

	it('preserves exact monetary range changes beyond JavaScript safe integers', () => {
		const change = portfolioChange([
			entry('9007199254740993.01', '2026-07-27T12:00:00Z'),
			entry('9007199254740992.01', '2026-07-27T10:00:00Z')
		]);

		expect(change?.amount).toBe('1');
		expect(change?.direction).toBe('gain');
	});

	it('calculates percentages without overflowing JavaScript Number', () => {
		const baseline = `1${'0'.repeat(400)}`;
		const current = `15${'0'.repeat(399)}`;
		const change = portfolioChange([
			entry(current, '2026-07-27T12:00:00Z'),
			entry(baseline, '2026-07-27T10:00:00Z')
		]);

		expect(change).toEqual({ amount: `5${'0'.repeat(399)}`, percent: '50.00', direction: 'gain' });
	});

	it('marks a worker stale after two configured sampling intervals', () => {
		const now = Date.parse('2026-07-27T12:11:00Z');
		const entries = [entry('100', '2026-07-27T12:00:00Z')];

		expect(isHistoryStale(entries, 300, now)).toBe(true);
	});
});
