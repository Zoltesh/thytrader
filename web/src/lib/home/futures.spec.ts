import { describe, expect, it } from 'vitest';
import { futuresCardView, SHARED_BUYING_POWER_NOTE, type FuturesAccountReport } from './futures';

function report(overrides: Partial<FuturesAccountReport['payload']> = {}): FuturesAccountReport {
	return {
		overall_status: 'healthy',
		components: [{ name: 'futures_account', status: 'healthy', reason_code: 'OK', detail: '' }],
		payload: {
			observed_at: '2026-10-10T02:00:00Z',
			age_seconds: 20,
			stale: false,
			enablement: 'enabled',
			read_failures: [],
			balance: {
				currency: 'USD',
				futures_buying_power: '1000.00',
				available_margin: '1000.00',
				liquidation_threshold: '0',
				liquidation_buffer_amount: '1000.00',
				liquidation_buffer_percentage: '100',
				funding_pnl: '-0.04',
				unrealized_pnl: '0'
			},
			positions: [],
			margin_ratio: null,
			...overrides
		}
	};
}

describe('futuresCardView', () => {
	it('shows USD facts, the shared-collateral note and a flat account', () => {
		const view = futuresCardView(report());
		expect(view.kind).toBe('account');
		if (view.kind !== 'account') return;
		expect(view.facts).toEqual([
			{ label: 'Buying power', value: '$1,000.00', hint: 'Shared with USDC spot' },
			{ label: 'Margin ratio', value: '—', hint: 'No open positions' },
			{ label: 'Liquidation buffer', value: '$1,000.00', hint: '100.00%' },
			{ label: 'Funding PnL', value: '−$0.04', hint: 'As reported by Coinbase, USD' }
		]);
		expect(view.sharedNote).toBe(SHARED_BUYING_POWER_NOTE);
		expect(view.positions).toBe('No open futures positions');
	});

	it('keeps unknown positions unknown and hides never-observed futures', () => {
		const unknown = futuresCardView(report({ positions: null, enablement: 'unknown' }));
		expect(unknown.kind === 'account' && unknown.positions).toContain('unknown');
		const hidden = futuresCardView({
			...report({ enablement: null }),
			components: [
				{ name: 'futures_account', status: 'degraded', reason_code: 'STORE_DISABLED', detail: '' }
			]
		});
		expect(hidden.kind).toBe('hidden');
	});
});
