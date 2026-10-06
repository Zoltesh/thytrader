import { describe, expect, it } from 'vitest';
import {
	comparableLabel,
	evidenceReasonLabel,
	liquidityLabel,
	slippageLabel,
	type ExecutionTwinComparison
} from './executionQuality';

describe('execution quality presentation', () => {
	it('does not render missing liquidity or slippage as zero', () => {
		expect(liquidityLabel(null)).toBe('not recorded');
		expect(liquidityLabel('maker')).toBe('maker');
		expect(slippageLabel(null)).toBe('no journaled close');
		expect(slippageLabel('-12.5')).toBe('-12.5 bps');
	});

	it('labels incomplete twin evidence as cannot-compare', () => {
		const comparison = {
			schema_version: 'thytrader-execution-twin-comparison-v1',
			comparable: false,
			reasons: ['incomplete_live_evidence'],
			paper: { deployment_id: 'p', net_pnl: '1', entry_fees: '0.1', exit_fees: '0.1' },
			live: { deployment_id: 'l', net_pnl: '1', entry_fees: '0.2', exit_fees: '0.2' },
			fee_normalization: null
		} satisfies ExecutionTwinComparison;
		expect(comparableLabel(comparison)).toBe('Cannot compare');
		expect(evidenceReasonLabel('fill_without_journaled_close')).toContain('journaled');
	});
});
