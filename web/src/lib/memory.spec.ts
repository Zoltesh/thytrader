import { describe, expect, it, vi } from 'vitest';

import { fetchMemoryStatus, fetchTradeReasons } from './memory';

describe('memory client', () => {
	it('loads redacted memory status without posting mutations', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({
				schema_version: 'thytrader-experiential-memory-v1',
				counts: { journals: 1, sentiment: 0, patterns: 0, notifications: 0, trade_reasons: 1 },
				notify_provider: 'none',
				notify_webhook_configured: false,
				notify_enabled: false,
				storage: 'available'
			})
		});
		vi.stubGlobal('fetch', fetchMock);
		const status = await fetchMemoryStatus();
		expect(status.notify_provider).toBe('none');
		expect(status.notify_webhook_configured).toBe(false);
		expect(status.counts.trade_reasons).toBe(1);
		expect(fetchMock).toHaveBeenCalledWith('/api/v1/memory', {
			headers: { Accept: 'application/json' }
		});
		vi.unstubAllGlobals();
	});

	it('loads why-trade records without posting notes', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({
				schema_version: 'thytrader-trade-reason-v1',
				trade_reasons: [
					{
						schema_version: 'thytrader-trade-reason-v1',
						id: '01985cf0-7b60-7000-8000-000000000021',
						created_at: '2026-09-16T12:00:00Z',
						origin: 'human',
						intent_id: '01985cf0-7b60-7000-8000-000000000022',
						deployment_id: '01985cf0-7b60-7000-8000-0000000000aa',
						deployment_kind: 'discretionary',
						mode: 'paper',
						product_id: 'BTC-USD',
						purpose: 'entry',
						side: 'buy',
						strategy: null,
						signal: {
							kind: 'discretionary',
							last_signal: 'discretionary',
							candle_starts_at: '2026-09-16T12:00:00Z',
							timeframe: '5m'
						},
						risk: {
							decision: 'allow',
							reason_code: 'ALLOWED',
							detail: 'Admitted.',
							policy_fingerprint: `sha256:${'b'.repeat(64)}`,
							policy_source: 'compiled_default'
						},
						notes: [],
						reconcile: {
							order_id: null,
							order_status: null,
							filled_quantity: null,
							reject_reason: null,
							unknown_timeout: false,
							ledger_available: true,
							fills: []
						}
					}
				]
			})
		});
		vi.stubGlobal('fetch', fetchMock);
		const rows = await fetchTradeReasons({
			deploymentId: '01985cf0-7b60-7000-8000-0000000000aa'
		});
		expect(rows[0]?.signal.kind).toBe('discretionary');
		expect(fetchMock).toHaveBeenCalledWith(
			'/api/v1/memory/trade-reasons?deployment_id=01985cf0-7b60-7000-8000-0000000000aa',
			{ headers: { Accept: 'application/json' } }
		);
		vi.unstubAllGlobals();
	});
});
