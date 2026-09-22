import { describe, expect, it } from 'vitest';

import type { HistoryEntry } from './portfolio';
import { portfolioWindowSummary } from './portfolio-summary';

function entry(amount: string): HistoryEntry {
	return { as_of: '2026-09-22T00:00:00+00:00', total_value: { amount, currency: 'USD' } };
}

describe('portfolioWindowSummary', () => {
	it('computes signed change and percent across the window', () => {
		const summary = portfolioWindowSummary([entry('10450.5'), entry('10000')]);
		expect(summary.direction).toBe('gain');
		expect(summary.changeAmount).toBe('450.5');
		expect(summary.changePercent).toBe('+4.51%'); // round-half-up on the 4th decimal scaled value
		expect(summary.sampleCount).toBe(2);
	});

	it('reports losses with a negative percent', () => {
		const summary = portfolioWindowSummary([entry('9800'), entry('10000')]);
		expect(summary.direction).toBe('loss');
		expect(summary.changeAmount).toBe('-200');
		expect(summary.changePercent).toBe('-2.00%');
	});

	it('treats a flat window as flat, not gain or loss', () => {
		const summary = portfolioWindowSummary([entry('100.00'), entry('100.00')]);
		expect(summary.direction).toBe('flat');
		expect(summary.changePercent).toBe('+0.00%');
	});

	it('never invents a trend from a single sample', () => {
		const summary = portfolioWindowSummary([entry('100')]);
		expect(summary.changeAmount).toBe('0');
		expect(summary.changePercent).toBeNull();
		expect(summary.direction).toBe('flat');
		expect(summary.sampleCount).toBe(1);
	});

	it('discloses sample counts for sparse windows', () => {
		const summary = portfolioWindowSummary([entry('100'), entry('101'), entry('102')]);
		// Newest-first: current 100 against baseline 102 is a decline.
		expect(summary.sampleCount).toBe(3);
		expect(summary.direction).toBe('loss');
		expect(summary.changeAmount).toBe('-2');
	});

	it('keeps exact decimal math without float drift', () => {
		const summary = portfolioWindowSummary([entry('0.30000000000000004'), entry('0.3')]);
		// Exact decimal strings: the 4e-17 difference is real and shown, not
		// rounded away into a fake flat line (float math would have lied here).
		expect(summary.direction).toBe('gain');
		expect(summary.changeAmount).toBe('0.00000000000000004');
	});
});
