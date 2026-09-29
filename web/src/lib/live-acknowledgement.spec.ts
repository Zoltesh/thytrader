import { afterEach, describe, expect, it, vi } from 'vitest';
import { createDeployment, placeDiscretionaryOrder, resumeDeployment } from './deployments';
import { lifecycleDialog } from './deployment-detail';
import type { Deployment } from './deployments';

type Captured = { url: string; body: unknown };

function stubFetch(): Captured[] {
	const calls: Captured[] = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/v1/security/session') {
				return new Response(JSON.stringify({ csrf_token: 'token' }), { status: 200 });
			}
			calls.push({
				url,
				body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
			});
			return new Response(JSON.stringify({ id: 'dep', mode: 'live' }), { status: 200 });
		})
	);
	return calls;
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('live acknowledgement on HTTP mutations', () => {
	it('createDeployment forwards i_understand_live for live', async () => {
		const calls = stubFetch();
		await createDeployment({
			strategy_fingerprint: 'sha256:' + 'a'.repeat(64),
			mode: 'live',
			i_understand_live: true
		});
		expect(calls[0].body).toMatchObject({ mode: 'live', i_understand_live: true });
	});

	it('resumeDeployment sends the ack body only when acknowledged', async () => {
		const calls = stubFetch();
		await resumeDeployment('dep');
		await resumeDeployment('dep', { liveAcknowledged: true });
		expect(calls[0].body).toBeUndefined();
		expect(calls[1].body).toEqual({ i_understand_live: true });
	});

	it('placeDiscretionaryOrder forwards i_understand_live for live', async () => {
		const calls = stubFetch();
		await placeDiscretionaryOrder({
			mode: 'live',
			product_id: 'BTC-USDC',
			stop_price: '90000',
			take_profit_price: '120000',
			idempotency_key: 'key',
			origin: 'human',
			i_understand_live: true
		});
		expect(calls[0].body).toMatchObject({ i_understand_live: true });
	});

	it('live resume dialog names real Coinbase order submission', () => {
		const deployment = {
			id: 'dep',
			mode: 'live',
			status: 'paused',
			timeframe: '5m',
			product_id: 'BTC-USDC',
			orders: [],
			fills: []
		} as unknown as Deployment;
		const dialog = lifecycleDialog(deployment, 'resume');
		expect(dialog.body.join('\n')).toContain('REAL Coinbase spot order submission');
		expect(dialog.danger).toBe(true);
	});
});
