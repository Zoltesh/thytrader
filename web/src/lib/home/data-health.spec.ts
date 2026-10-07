import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchDataHealth, tailDescription, type WatchedTail } from './data-health';

const row: WatchedTail = {
	product_id: 'ETH-USD',
	timeframe: '6h',
	provider: 'coinbase',
	expected_closed_end: '2026-10-06T00:00:00Z',
	covered_ends_at: '2026-10-06T00:00:00Z',
	tail_state: 'fresh',
	lag_seconds: 0,
	missing_closed_bars: 0,
	settlement_deadline: '2026-10-06T00:02:00Z',
	watch_complete: false,
	island_complete: true,
	worker_status: 'succeeded',
	failure_code: null
};

afterEach(() => vi.unstubAllGlobals());

describe('watched-market tails', () => {
	it('does not turn incomplete history into a stale tail', () => {
		expect(tailDescription(row)).toBe('Current closed candle');
	});
	it('does not turn worker success into a fresh tail', () => {
		expect(tailDescription({ ...row, tail_state: 'stale', missing_closed_bars: 3 })).toBe(
			'Stale · 3 closed bars behind'
		);
	});
	it('labels missing, invalid and settling without green guesses', () => {
		expect(tailDescription({ ...row, tail_state: 'missing' })).toContain('Missing');
		expect(tailDescription({ ...row, tail_state: 'invalid' })).toContain('Invalid');
		expect(tailDescription({ ...row, tail_state: 'settling' })).toContain('settling');
	});
	it('uses the all-watch endpoint and preserves degraded inventory evidence', async () => {
		const payload = { overall_status: 'degraded', payload: { inventory_complete: false } };
		const read = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
		vi.stubGlobal('fetch', read);
		expect(await fetchDataHealth()).toEqual(payload);
		expect(read).toHaveBeenCalledWith('/api/v1/operator/data-health', expect.anything());
	});
	it('does not replace failed reads with empty success or raw error bodies', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
		await expect(fetchDataHealth()).rejects.toThrow('HTTP 503');
	});
});
