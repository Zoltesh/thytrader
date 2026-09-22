import { describe, expect, it } from 'vitest';

import type { PortfolioAsset } from './portfolio';
import {
	DUST_THRESHOLD,
	analyzeDust,
	formatQuantityDisplay,
	groupIntegerDigits,
	snapshotAgeMinutes,
	sumDecimalStrings
} from './money';

function asset(currency: string, value: string | null): PortfolioAsset {
	return {
		currency,
		name: currency,
		available: '1',
		hold: '0',
		total: '1',
		value: value === null ? null : { amount: value, currency: 'USD' }
	};
}

describe('formatQuantityDisplay', () => {
	it('keeps meaningful precision for values at or above one', () => {
		expect(formatQuantityDisplay('397.9503531252607675').text).toBe('397.9504');
		expect(formatQuantityDisplay('0.76000000').text).toBe('0.76');
		expect(formatQuantityDisplay('8116769').text).toBe('8,116,769');
	});

	it('keeps ultra-small values meaningful instead of rounding to zero', () => {
		// 13 leading zeros, then three significant digits ('392' rounds to '393').
		expect(formatQuantityDisplay('0.00000000000003928166').text).toBe('0.0000000000000393');
		// Two leading zeros, then three significant digits.
		expect(formatQuantityDisplay('0.0071635724061339').text).toBe('0.00716');
	});

	it('renders exact zero compactly', () => {
		expect(formatQuantityDisplay('0').text).toBe('0');
		expect(formatQuantityDisplay('0.00000000').text).toBe('0');
	});

	it('always discloses the raw string when rounding occurs', () => {
		const display = formatQuantityDisplay('397.9503531252607675');
		expect(display.compact).toBe(true);
		expect(display.title).toBe('397.9503531252607675');
		expect(formatQuantityDisplay('0.76').compact).toBe(false);
	});
});

describe('analyzeDust', () => {
	it('splits rows at the threshold and sums dust exactly', () => {
		const analysis = analyzeDust([
			asset('USDC', '397.9503531252607675'),
			asset('BONK', '27.19'),
			asset('AXS', '0.08'),
			asset('OP', '0.0005')
		]);
		expect(analysis.visible.map((a) => a.currency)).toEqual(['USDC', 'BONK']);
		expect(analysis.dust.map((a) => a.currency)).toEqual(['AXS', 'OP']);
		expect(analysis.dustTotal).toBe('0.0805');
		expect(DUST_THRESHOLD).toBe('0.10');
	});

	it('keeps unvalued rows visible — they are a disclosure, not dust', () => {
		const analysis = analyzeDust([asset('XYZ', null)]);
		expect(analysis.visible).toHaveLength(1);
		expect(analysis.dust).toHaveLength(0);
		expect(analysis.dustTotal).toBeNull();
	});

	it('sums floating-point-hostile decimals exactly', () => {
		expect(sumDecimalStrings(['0.1', '0.2'])).toBe('0.3');
		expect(sumDecimalStrings(['0.30000000000000004', '-0.00000000000000004'])).toBe('0.3');
	});
});

describe('groupIntegerDigits', () => {
	it('groups whole digits without touching fractions', () => {
		expect(groupIntegerDigits('8116769')).toBe('8,116,769');
		expect(groupIntegerDigits('1234.5678')).toBe('1,234.5678');
	});
});

describe('snapshotAgeMinutes', () => {
	it('floors at zero for clock skew', () => {
		const now = Date.parse('2026-09-22T12:00:00Z');
		expect(snapshotAgeMinutes('2026-09-22T11:30:00Z', now)).toBe(30);
		expect(snapshotAgeMinutes('2026-09-22T12:00:30Z', now)).toBe(0);
		expect(snapshotAgeMinutes('not-a-date', now)).toBeNull();
	});
});
