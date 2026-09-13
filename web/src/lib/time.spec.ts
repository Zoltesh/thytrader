import { describe, expect, it } from 'vitest';

import { formatUtcTimestamp } from './time';

describe('UTC evidence timestamps', () => {
	it('renders an ISO instant as an explicit UTC clock reading', () => {
		expect(formatUtcTimestamp('2026-08-01T03:00:00Z')).toBe('2026-08-01 03:00:00 UTC');
		expect(formatUtcTimestamp('2026-08-17T12:00:00.000Z')).toBe('2026-08-17 12:00:00 UTC');
	});
});
