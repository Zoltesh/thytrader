import { afterEach, describe, expect, it, vi } from 'vitest';
import { protectionBadge, type ProtectionEvidence } from './protection-evidence';

const venue: ProtectionEvidence = {
	required_quantity: '0.5',
	covered_quantity: '0.5',
	uncovered_quantity: '0',
	stop_side: 'sell',
	stop_side_valid: true,
	stop_geometry_valid: true,
	mechanism: 'venue',
	venue_resting: true,
	worker_dependent: false,
	observed_at: '2026-10-06T12:00:00+00:00',
	verified_at: '2026-10-06T12:00:00+00:00',
	observation_source: 'venue_order_state',
	freshness: 'recent_venue',
	evaluated_at: '2026-10-06T12:00:00+00:00',
	freshness_max_age_seconds: 120,
	geometry_basis: 'working_target',
	reasons: ['venue_stop_resting']
};

describe('protection badges (ADR 0112)', () => {
	afterEach(() => vi.useRealTimers());
	it('does not paint a paper synthetic stop as a venue-resting green badge', () => {
		const badge = protectionBadge({
			position_state: 'open_protected',
			protection_status: 'covered',
			target_price: '3400',
			protection: {
				...venue,
				mechanism: 'synthetic',
				venue_resting: false,
				worker_dependent: true,
				observed_at: null,
				verified_at: null,
				reasons: ['synthetic_worker_dependent']
			}
		});
		expect(badge.text).toBe('Worker stop');
		expect(badge.tone).toBe('warn');
		expect(badge.tone).not.toBe('ok');
		expect(badge.detail).toContain('not venue-resting');
		expect(badge.detail).toContain('time unknown');
	});

	it('does not treat a take-profit-only or partial stop as covered', () => {
		const takeProfit = protectionBadge({
			position_state: 'open_unprotected',
			protection_status: 'unprotected',
			target_price: '3400',
			protection: {
				...venue,
				covered_quantity: '0',
				uncovered_quantity: '0.5',
				mechanism: 'none',
				venue_resting: false,
				stop_side_valid: false,
				stop_geometry_valid: false,
				verified_at: null,
				reasons: ['take_profit_only', 'no_resting_stop']
			}
		});
		expect(takeProfit.text).toBe('Unprotected');
		expect(takeProfit.tone).toBe('bad');
		const partial = protectionBadge({
			protection_status: 'unprotected',
			protection: {
				...venue,
				covered_quantity: '0.3',
				uncovered_quantity: '0.2',
				reasons: ['partial_stop_quantity']
			}
		});
		expect(partial.text).toBe('Unprotected');
		expect(partial.detail).toContain('0.3 of 0.5');
	});

	it('labels fresh order state without claiming audited venue geometry', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2026-10-06T12:00:00Z'));
		expect(
			protectionBadge({
				protection_status: 'covered',
				target_price: '3400',
				protection: venue
			}).text
		).toBe('Order state fresh');
		expect(
			protectionBadge({
				protection_status: 'covered',
				target_price: null,
				protection: venue
			}).text
		).toBe('Order state fresh');
		const fresh = protectionBadge({ protection_status: 'covered', protection: venue });
		expect(fresh.tone).toBe('warn');
		expect(fresh.detail).toContain('oldest contributing order-state receipt');
		expect(fresh.title).toContain('submitted geometry: working_target');
		expect(fresh.title).toContain('venue geometry not independently verified');
		expect(fresh.title).not.toContain('venue stop · verified');
		const pending = protectionBadge({
			protection_status: 'unknown',
			protection: {
				...venue,
				covered_quantity: '0',
				uncovered_quantity: '0.5',
				mechanism: 'unverified',
				venue_resting: false,
				stop_side_valid: false,
				stop_geometry_valid: false,
				verified_at: null,
				reasons: ['pending_not_confirmed']
			}
		});
		expect(pending.text).toBe('Unverified');
		expect(pending.tone).toBe('warn');
	});

	it('keeps exiting distinct from both synthetic and venue cover', () => {
		for (const mechanism of ['venue', 'synthetic'] as const) {
			const badge = protectionBadge({
				position_state: 'exiting',
				protection_status: 'covered',
				protection: { ...venue, mechanism }
			});
			expect(badge.text).toBe('Exiting');
			expect(badge.tone).toBe('warn');
		}
	});

	it('never treats recent local writes, stale verification, or missing evidence as green', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2026-10-06T12:00:00Z'));
		for (const evidence of [
			{ ...venue, verified_at: null, reasons: ['local_observation_only'] },
			{ ...venue, freshness: 'stale' as const },
			{ ...venue, verified_at: '2026-10-06T11:55:00Z' },
			{ ...venue, verified_at: '2026-10-06T12:01:00Z' },
			{ ...venue, verified_at: 'not-a-time' },
			{
				...venue,
				observation_source: 'persisted_order' as const,
				freshness: 'recent_local' as const
			}
		]) {
			const badge = protectionBadge({ protection_status: 'covered', protection: evidence });
			expect(badge.text).toBe('Unverified');
			expect(badge.tone).toBe('warn');
		}
		const persisted = protectionBadge({
			protection_status: 'covered',
			protection: {
				...venue,
				verified_at: null,
				observation_source: 'persisted_order',
				freshness: 'recent_local',
				reasons: ['local_observation_only']
			}
		});
		expect(persisted.detail).toContain('venue order-state freshness unverified');
		expect(persisted.detail).toContain('recent_local');
		vi.setSystemTime(new Date('2026-10-06T12:02:01Z'));
		const frozen = protectionBadge({ protection_status: 'covered', protection: venue });
		expect(frozen.text).toBe('Unverified');
		expect(frozen.tone).toBe('warn');
		const legacy = protectionBadge({ position_state: 'open_protected' });
		expect(legacy.text).toBe('Protected · unverified');
		expect(legacy.tone).toBe('warn');
	});
});
