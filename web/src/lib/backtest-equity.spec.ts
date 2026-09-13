import { describe, expect, it } from 'vitest';

import { backtestEquityChartModel, type EquityPoint } from './backtests';

describe('backtest equity chart geometry', () => {
	function point(equity: string, at: string): EquityPoint {
		return {
			candle_starts_at: at,
			cash: equity,
			base_quantity: '0',
			mark_price: '1',
			equity
		};
	}

	it('keeps zero and the smallest valid equity amount at distinct finite chart positions', () => {
		const tinyEquity = `0.${'0'.repeat(6205)}1`;
		const model = backtestEquityChartModel([
			point('0', '2026-08-01T02:00:00Z'),
			point(tinyEquity, '2026-08-01T03:00:00Z')
		]);

		expect(model.series).toHaveLength(2);
		expect(model.samples.map((sample) => sample.value).every(Number.isFinite)).toBe(true);
		expect(model.samples[0]?.value).not.toBe(model.samples[1]?.value);
	});

	it('returns an empty model for fewer than two equity observations', () => {
		expect(backtestEquityChartModel([]).series).toEqual([]);
		expect(backtestEquityChartModel([point('10000', '2026-08-01T02:00:00Z')]).series).toEqual([]);
	});

	it('orders equity points by evaluation time', () => {
		const model = backtestEquityChartModel([
			point('10000', '2026-08-01T02:00:00Z'),
			point('10100', '2026-08-01T04:00:00Z'),
			point('10050', '2026-08-01T03:00:00Z')
		]);

		expect(model.samples.map((sample) => sample.date)).toEqual([
			'2026-08-01T02:00:00Z',
			'2026-08-01T03:00:00Z',
			'2026-08-01T04:00:00Z'
		]);
		expect(model.samples.map((sample) => sample.amount)).toEqual(['10000', '10050', '10100']);
	});
});
