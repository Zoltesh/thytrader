import { describe, expect, it } from 'vitest';
import { formatBrokerAssumptions, formatPercent, shortFingerprint } from './backtests';

describe('backtest presentation', () => {
	it('formats stored return fractions as percentages for display', () => {
		expect(formatPercent('0.01981496505')).toBe('1.98%');
	});

	it('shortens a canonical fingerprint without discarding its identity prefix', () => {
		const fingerprint = `sha256:${'a'.repeat(64)}`;
		expect(shortFingerprint(fingerprint)).toBe(`sha256:${'a'.repeat(9)}…${'a'.repeat(8)}`);
	});

	it('renders disclosed V2 broker assumptions instead of inventing a spread', () => {
		expect(
			formatBrokerAssumptions({
				price_model: 'constant_spread_bps',
				spread_bps: '10',
				fill_policy: 'full',
				trigger_evaluation: 'bid_side',
				equity_marking: 'bid_close'
			})
		).toContain('10 bps constant spread');
	});
});
