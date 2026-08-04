import { describe, expect, it } from 'vitest';
import {
	chartData,
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
		expect(result.points.split(' ')).toHaveLength(2);
	});

	it('splits visual segments when snapshots have a worker-downtime gap', () => {
		const entries = [
			entry('100', '2026-07-27T10:00:00Z'),
			entry('110', '2026-07-27T10:05:00Z'),
			entry('120', '2026-07-27T11:00:00Z'),
			entry('130', '2026-07-27T11:05:00Z')
		];

		const segments = chartSegments(chartData(entries, 760, 220, 40, 600));

		expect(segments).toHaveLength(2);
		expect(segments.every((segment) => segment.split(' ').length === 2)).toBe(true);
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

		expect(change).toEqual({ amount: 25, percent: 25, direction: 'gain' });
	});

	it('marks a worker stale after two configured sampling intervals', () => {
		const now = Date.parse('2026-07-27T12:11:00Z');
		const entries = [entry('100', '2026-07-27T12:00:00Z')];

		expect(isHistoryStale(entries, 300, now)).toBe(true);
	});
});
