import { describe, expect, it } from 'vitest';

import { formatUtcMinute, formatUtcTimestamp } from './time';

describe('UTC evidence timestamps', () => {
	it('renders an ISO instant as an explicit UTC clock reading', () => {
		expect(formatUtcTimestamp('2026-08-01T03:00:00Z')).toBe('2026-08-01 03:00:00 UTC');
		expect(formatUtcTimestamp('2026-08-17T12:00:00.000Z')).toBe('2026-08-17 12:00:00 UTC');
	});
});

describe('UTC minute readings', () => {
	it('renders approximate instants to the minute, from ISO text or a Date', () => {
		expect(formatUtcMinute('2026-09-22T00:00:59Z')).toBe('2026-09-22 00:00 UTC');
		expect(formatUtcMinute('2026-09-21T22:30:00+02:00')).toBe('2026-09-21 20:30 UTC');
		expect(formatUtcMinute(new Date(Date.UTC(2026, 8, 21, 20, 2)))).toBe('2026-09-21 20:02 UTC');
	});
});
