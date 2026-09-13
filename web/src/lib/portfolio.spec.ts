import { describe, expect, it } from 'vitest';
import {
	chartData,
	chartHasGaps,
	chartSegments,
	formatUsd,
	isHistoryStale,
	permissionLabel,
	portfolioChange,
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

describe('chartData', () => {
	function entry(amount: string, asOf: string): HistoryEntry {
		return { as_of: asOf, total_value: { amount, currency: 'USD' } };
	}

	it('returns empty result for fewer than 2 entries', () => {
		const result = chartData([entry('100', '2026-07-27T10:00:00Z')], 760, 220, 40);
		expect(result.points).toBe('');
		expect(result.values).toEqual([]);
	});

	it('computes SVG points for a normal dataset', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('200', '2026-07-27T11:00:00Z'),
			entry('150', '2026-07-27T12:00:00Z')
		];
		const result = chartData(entries, 760, 220, 40);
		expect(result.values).toEqual([100, 200, 150]);
		expect(result.min).toBe(100);
		expect(result.max).toBe(200);
		expect(result.points.split(' ')).toHaveLength(3);
		expect(result.dates).toHaveLength(3);
	});

	it('handles flat line where all values are identical', () => {
		const entries = [entry('500', '2026-07-27T10:00:00Z'), entry('500', '2026-07-27T11:00:00Z')];
		const result = chartData(entries, 760, 220, 40);
		expect(result.min).toBe(500);
		expect(result.max).toBe(500);
		expect(result.points).toBeTruthy();
		// Both points should be at the same Y (bottom of chart area since range collapses to 1)
		expect(result.points.split(' ')).toHaveLength(2);
	});

	it('handles exact decimal strings with high precision', () => {
		const entries = [
			entry('12345.678901', '2026-07-27T10:00:00Z'),
			entry('98765.432109', '2026-07-27T11:00:00Z')
		];
		const result = chartData(entries, 760, 220, 40);
		expect(result.min).toBeCloseTo(12345.678901);
		expect(result.max).toBeCloseTo(98765.432109);
		expect(result.minAmount).toBe('12345.678901');
		expect(result.maxAmount).toBe('98765.432109');
		expect(result.coordinates[0].amount).toBe('12345.678901');
		expect(result.points.split(' ')).toHaveLength(2);
	});

	it('keeps huge and tiny exact amounts finite in SVG geometry', () => {
		const tiny = `0.${'0'.repeat(500)}1`;
		const huge = `1${'0'.repeat(500)}`;
		const result = chartData(
			[entry(tiny, '2026-07-27T10:00:00Z'), entry(huge, '2026-07-27T11:00:00Z')],
			760,
			220,
			40
		);

		expect(result.values.every(Number.isFinite)).toBe(true);
		expect(result.coordinates.every(({ x, y, value }) => Number.isFinite(x + y + value))).toBe(
			true
		);
		expect(result.points).not.toContain('Infinity');
		expect(result.minAmount).toBe(tiny);
		expect(result.maxAmount).toBe(huge);
		expect(result.coordinates[0].y).toBeGreaterThan(result.coordinates[1].y);
	});

	it('splits visual segments when snapshots have a worker-downtime gap', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T10:05:00Z'),
			entry('120', '2026-07-27T11:00:00Z'),
			entry('130', '2026-07-27T11:05:00Z')
		];

		const data = chartData(entries, 760, 220, 40, 600);
		const segments = chartSegments(data);

		expect(segments).toHaveLength(2);
		expect(segments.every((segment) => segment.split(' ').length === 2)).toBe(true);
		expect(data.hasGaps).toBe(true);
		expect(chartHasGaps(data.coordinates)).toBe(true);
	});

	it('places X by wall-clock time so a long gap occupies more space than nearby samples', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T11:00:00Z'),
			entry('120', '2026-07-27T14:00:00Z')
		];
		const result = chartData(entries, 760, 220, 40);
		const xs = result.coordinates.map((coordinate) => coordinate.x);
		const padding = 40;
		const chartW = 680;

		expect(xs[0]).toBe(padding);
		expect(xs[1]).toBe(padding + chartW * 0.25);
		expect(xs[2]).toBe(padding + chartW);
		// Index spacing would put the middle sample at 50% instead of 25%.
		expect(xs[1]).toBeLessThan(padding + chartW * 0.5);
	});

	it('keeps equally spaced snapshots equally spaced on X', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T11:00:00Z'),
			entry('120', '2026-07-27T12:00:00Z')
		];
		const result = chartData(entries, 760, 220, 40, 3600);
		const xs = result.coordinates.map((coordinate) => coordinate.x);

		expect(xs[1] - xs[0]).toBeCloseTo(xs[2] - xs[1]);
		expect(result.hasGaps).toBe(false);
	});

	it('keeps an orphan post-gap snapshot as a dot, without Y interpolation, and reports the gap', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T10:05:00Z'),
			entry('120', '2026-07-27T11:00:00Z')
		];
		const data = chartData(entries, 760, 220, 40, 300);
		const segments = chartSegments(data);
		const xs = data.coordinates.map((coordinate) => coordinate.x);
		const padding = 40;
		const chartW = 680;

		expect(data.coordinates).toHaveLength(3);
		expect(data.coordinates[2]?.gapBefore).toBe(true);
		expect(data.hasGaps).toBe(true);
		expect(chartHasGaps(data.coordinates)).toBe(true);
		expect(segments).toHaveLength(1);
		expect(segments[0]?.split(' ')).toHaveLength(2);
		expect(xs[1]).toBeCloseTo(padding + chartW * (5 / 60));
		expect(xs[2]).toBe(padding + chartW);
		expect(data.coordinates[2]?.y).not.toBe(data.coordinates[1]?.y);
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
