import { render } from 'svelte/server';
import { expect, it } from 'vitest';
import ExecutionQualityReportView from './ExecutionQualityReport.svelte';
import type { ExecutionQualityReport, ExecutionTwinComparison } from './executionQuality';

const entry = {
	fill_id: 'entry',
	order_id: 'buy',
	side: 'buy' as const,
	price: '100',
	quantity: '1',
	fee: '0.1',
	filled_at: '2026-01-01T01:00:00Z',
	liquidity: 'maker' as const,
	slippage_bps: '0',
	reference_price: '100',
	reference_intent_id: 'intent',
	reference_bar_starts_at: '2026-01-01T00:00:00Z',
	reference_bar_closes_at: '2026-01-01T01:00:00Z'
};
const exit = {
	...entry,
	fill_id: 'partial-exit',
	order_id: 'sell',
	side: 'sell' as const,
	price: '110',
	quantity: '0.4',
	fee: '0.044',
	liquidity: null,
	slippage_bps: null,
	reference_price: null,
	reference_intent_id: null,
	reference_bar_starts_at: null,
	reference_bar_closes_at: null
};
const report: ExecutionQualityReport = {
	schema_version: 'thytrader-execution-quality-v1',
	report_fingerprint: 'test',
	deployment_id: 'live',
	product_id: 'BTC-USD',
	mode: 'live',
	status: 'running',
	strategy_fingerprint: 'snapshot',
	timeframe: '1h',
	books: [
		{
			product_id: 'BTC-USD',
			closed_trade_count: 0,
			round_trips: [],
			recorded_fills: [entry, exit],
			open_cycle: {
				direction: 'long',
				quantity: '0.6',
				entry_fees: '0.1',
				position_matches_ledger: true,
				exits: [exit]
			},
			fill_price_pnl_before_fees: '0',
			entry_fees: '0',
			exit_fees: '0',
			net_pnl: '0'
		}
	],
	totals: {
		closed_trade_count: 0,
		open_cycle_count: 1,
		fill_price_pnl_before_fees: '0',
		entry_fees: '0',
		exit_fees: '0',
		net_pnl: '0',
		ledger_realized_delta: '3.916',
		weighted_slippage_bps: '0',
		slippage_fills_journaled: 1,
		slippage_fills_total: 2
	},
	evidence: {
		complete: false,
		reasons: ['open_cycle_present', 'fill_without_journaled_close'],
		unapplied_fill_count: 0,
		orphan_fill_count: 0
	}
};
const comparison: ExecutionTwinComparison = {
	schema_version: 'thytrader-execution-twin-comparison-v1',
	comparable: false,
	population: 'recorded_fill_lifetime',
	summaries_context_only: true,
	reasons: ['lifetime_windows_differ'],
	paper: { deployment_id: 'paper', net_pnl: '9.79', entry_fees: '0.1', exit_fees: '0.11' },
	live: { deployment_id: 'live', net_pnl: '0', entry_fees: '0', exit_fees: '0' },
	fee_normalization: {
		rate_source: 'stored_paper_assumptions',
		observed_live_fees: '0.144',
		counterfactual_live_fees_at_paper_rates: null,
		fee_delta: null,
		fills_without_liquidity_evidence: 1,
		population: 'live_applied_fill_lifetime',
		fill_count: 2,
		complete: false
	}
};

it('renders unknown counterfactual costs as unavailable and lifetime PnL as context only', () => {
	const { body } = render(ExecutionQualityReportView, { props: { report, comparison } });
	expect(body).toContain('Cannot compare twins');
	expect(body).toContain('context only');
	expect(body).toContain('unavailable (unknown liquidity or incomplete fill coverage)');
	expect(body).toContain('independently of');
	expect(body).toMatch(/Realized PnL is\s+unchanged/);
});

it('renders raw partial exits once, with causal price provenance or an explicit missing reference', () => {
	const { body } = render(ExecutionQualityReportView, { props: { report } });
	expect(body).toContain('including partial exits');
	expect(body.match(/sell 0\.4/g)).toHaveLength(1);
	expect(body).toContain('0.044');
	expect(body).toContain('bar completed');
	expect(body).toContain('No causal decision close');
});
