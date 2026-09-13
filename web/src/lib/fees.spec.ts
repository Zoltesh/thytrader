import { describe, expect, it } from 'vitest';

import { formatFeeProfileAsOf } from './fees';

describe('formatFeeProfileAsOf', () => {
	it('formats a present UTC as_of with the locale timestamp', () => {
		expect(formatFeeProfileAsOf('2026-08-17T12:00:00Z')).toBe(
			new Date('2026-08-17T12:00:00Z').toLocaleString()
		);
	});

	it('returns null when as_of is missing or not a timestamp', () => {
		expect(formatFeeProfileAsOf('')).toBeNull();
		expect(formatFeeProfileAsOf('   ')).toBeNull();
		expect(formatFeeProfileAsOf('not-a-timestamp')).toBeNull();
	});
});
