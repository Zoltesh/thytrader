import { describe, expect, it } from 'vitest';
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
	reasons: ['venue_stop_resting']
};

describe('protection badges (ADR 0112)', () => {
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

	it('keeps a matching venue bracket green and a pending stop unverified', () => {
		expect(
			protectionBadge({
				protection_status: 'covered',
				target_price: '3400',
				protection: venue
			}).text
		).toBe('Venue TP/SL');
		expect(
			protectionBadge({
				protection_status: 'covered',
				target_price: null,
				protection: venue
			}).text
		).toBe('Venue stop');
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

	it('keeps the legacy chip when a payload has no evidence yet', () => {
		expect(protectionBadge({ position_state: 'open_protected' }).text).toBe('Protected');
		expect(
			protectionBadge(
				{ position_state: 'open_protected', target_price: '12' },
				{ fallback: 'sentence' }
			).text
		).toBe('Open · protected (TP/SL resting)');
	});
});
