import { describe, expect, it } from 'vitest';
import { formatPercent, shortFingerprint } from './backtests';

describe('backtest presentation', () => {
	it('formats stored return fractions as percentages for display', () => {
		expect(formatPercent('0.01981496505')).toBe('1.98%');
	});

	it('shortens a canonical fingerprint without discarding its identity prefix', () => {
		const fingerprint = `sha256:${'a'.repeat(64)}`;
		expect(shortFingerprint(fingerprint)).toBe(`sha256:${'a'.repeat(9)}…${'a'.repeat(8)}`);
	});
});
