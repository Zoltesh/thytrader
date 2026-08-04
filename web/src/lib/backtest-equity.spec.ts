import { describe, expect, it } from 'vitest';

import { backtestEquityPath } from './backtests';

describe('backtest equity chart geometry', () => {
	it('keeps zero and the smallest valid equity amount at distinct finite chart positions', () => {
		const tinyEquity = `0.${'0'.repeat(6205)}1`;
		const coordinates = backtestEquityPath(['0', tinyEquity])
			.split(' ')
			.map((point) => point.split(',').map(Number));

		expect(coordinates).toHaveLength(2);
		expect(coordinates.flat().every(Number.isFinite)).toBe(true);
		expect(coordinates[0][1]).not.toBe(coordinates[1][1]);
	});
});
