import { describe, expect, it } from 'vitest';
import {
	comparableLabel,
	counterfactualFeeLabel,
	evidenceReasonLabel,
	liquidityLabel,
	slippageLabel,
	type ExecutionTwinComparison
} from './executionQuality';

describe('execution quality presentation', () => {
	it('does not render missing liquidity or slippage as zero', () => {
		expect(liquidityLabel(null)).toBe('not recorded');
		expect(liquidityLabel('maker')).toBe('maker');
		expect(slippageLabel(null)).toBe('no causal decision close');
		expect(counterfactualFeeLabel(null)).toContain('unavailable');
		expect(counterfactualFeeLabel(null)).not.toBe('0');
		expect(slippageLabel('-12.5')).toBe('-12.5 bps');
	});

	it('keeps unknown counterfactual fees distinct from an explicitly recorded zero', () => {
		const normalization = {
			rate_source: 'stored_paper_assumptions',
			observed_live_fees: '0.21',
			counterfactual_live_fees_at_paper_rates: null,
			fee_delta: null,
			fills_without_liquidity_evidence: 1,
			population: 'live_applied_fill_lifetime',
			fill_count: 2,
			complete: false
		} satisfies NonNullable<ExecutionTwinComparison['fee_normalization']>;
		expect(counterfactualFeeLabel(normalization.counterfactual_live_fees_at_paper_rates)).toContain(
			'unavailable'
		);
		expect(counterfactualFeeLabel('0')).toBe('0');
		expect(normalization.fee_delta).toBeNull();
		expect(normalization.observed_live_fees).toBe('0.21');
	});

	it('labels incomplete twin evidence as cannot-compare', () => {
		const comparison = {
			schema_version: 'thytrader-execution-twin-comparison-v1',
			comparable: false,
			population: 'recorded_fill_lifetime',
			summaries_context_only: true,
			reasons: ['incomplete_live_evidence'],
			paper: { deployment_id: 'p', net_pnl: '1', entry_fees: '0.1', exit_fees: '0.1' },
			live: { deployment_id: 'l', net_pnl: '1', entry_fees: '0.2', exit_fees: '0.2' },
			fee_normalization: null
		} satisfies ExecutionTwinComparison;
		expect(comparableLabel(comparison)).toBe('Cannot compare');
		expect(comparison.summaries_context_only).toBe(true);
		expect(evidenceReasonLabel('fill_without_journaled_close')).toContain('journaled');
	});
});
