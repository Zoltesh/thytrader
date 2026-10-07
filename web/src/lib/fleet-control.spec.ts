import { describe, expect, it } from 'vitest';
import { inventoryPageFromBody } from './deployments';
import { fleetEffect, fleetNeedsLiveAck, residualLabel, type FleetTarget } from './fleet-control';

describe('fleet control wording', () => {
	it('keeps disarm distinct from cancellation and flatten', () => {
		expect(fleetEffect('disarm')).toContain('Does not pause, cancel, or flatten');
		expect(fleetEffect('managed_stop')).toContain('Not a flatten');
		expect(fleetEffect('flatten')).toContain('explicit flatten');
		expect(fleetNeedsLiveAck('disarm', 'live')).toBe(false);
		expect(fleetNeedsLiveAck('flatten', 'live')).toBe(true);
		expect(fleetNeedsLiveAck('rearm', 'paper')).toBe(false);
		expect(fleetNeedsLiveAck('rearm', 'all')).toBe(true);
	});

	it('does not describe an unknown residual read as flat', () => {
		const target: FleetTarget = {
			deployment_id: 'bot',
			revision: 1,
			mode: 'paper',
			status: 'running',
			lifecycle_command: 'none',
			product_id: 'BTC-USDC',
			positions: null,
			positions_known: false,
			effect: 'unchanged'
		};
		expect(residualLabel(target)).toBe('Residual positions unknown');
	});
});

describe('deployment inventory pages', () => {
	it('trusts has_more false when a page is exactly full', () => {
		const page = inventoryPageFromBody(
			{
				deployments: [{ id: 'a' } as never],
				returned: 1,
				has_more: false,
				as_of: '2026-10-06T00:00:00+00:00',
				total: 1,
				order: 'created_at_desc_id_desc',
				fingerprint: 'fence',
				next_cursor: null
			},
			1
		);
		expect(page.hasMore).toBe(false);
		expect(page.asOf).toBe('2026-10-06T00:00:00+00:00');
	});

	it('treats a legacy full page without has_more as incomplete', () => {
		expect(() =>
			inventoryPageFromBody({ deployments: [{ id: 'a' } as never], returned: 1 }, 1)
		).toThrow(/incomplete/);
	});
});
