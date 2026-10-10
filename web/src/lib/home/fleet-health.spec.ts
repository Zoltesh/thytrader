import { describe, expect, it } from 'vitest';

import { fleetBanner, type FleetEntryScope, type FleetHealthReport } from './fleet-health';

const LEGACY = '01a0f90a-9834-7d0e-afad-ef0b538fee40';
const GAP =
	'order 01a0f93b-9591-7ee1-b037-eae7b079c1b6 FILLED with filled_quantity 0 but fills sum 0.00014174';

function scope(overrides: Partial<FleetEntryScope> = {}): FleetEntryScope {
	return {
		mode: 'live',
		scope: 'USDC',
		entries_admissible: 'yes',
		reason_codes: [],
		blocking_deployment_ids: [],
		running_deployments: 2,
		occupied_deployments: 2,
		alert_subject: 'fleet:live:USDC',
		checks: [],
		...overrides
	};
}

function report(
	scopes: FleetEntryScope[],
	extra: Partial<FleetHealthReport['payload']['decisions']> = {}
): FleetHealthReport {
	return {
		overall_status: 'healthy',
		payload: {
			entries: {
				evaluated_at: '2026-10-10T12:00:00Z',
				complete: true,
				detail: '',
				live_entries_admissible: 'yes',
				paper_entries_admissible: 'yes',
				scopes
			},
			decisions: { storage: 'available', window_hours: 24, systemic: [], ...extra }
		}
	};
}

const incident = scope({
	entries_admissible: 'blocked',
	reason_codes: ['BREAKER_MARK_MISSING'],
	blocking_deployment_ids: [LEGACY],
	checks: [
		{
			check: 'daily_loss',
			status: 'blocked',
			reason_code: 'BREAKER_MARK_MISSING',
			blocker_class: 'evidence',
			fleet_wide: true,
			detail: 'Daily-loss unavailable.',
			deployments: [
				{ deployment_id: LEGACY, status: 'stopped', product_id: 'BTC-USDC', detail: GAP }
			]
		}
	]
});

describe('fleetBanner', () => {
	it('hides when every occupied scope admits entries', () => {
		expect(fleetBanner(report([scope()]))).toBeNull();
	});

	it('shows the 2026-10-10 block in the danger tone with the exact order', () => {
		const banner = fleetBanner(report([incident]));
		expect(banner?.tone).toBe('danger');
		expect(banner?.title).toBe('New entries are blocked fleet-wide');
		const [view] = banner?.scopes ?? [];
		expect(view.title).toBe('Live USDC entries are blocked fleet-wide');
		expect(view.reasons).toBe('BREAKER_MARK_MISSING');
		expect(view.books).toEqual([
			{ deploymentId: LEGACY, label: 'BTC-USDC (stopped)', detail: GAP }
		]);
		expect(view.clears).toContain('repaired');
	});

	it('uses the warning tone for a paper latch', () => {
		const latched = scope({
			mode: 'paper',
			entries_admissible: 'blocked',
			reason_codes: ['DAILY_LOSS_LIMIT'],
			alert_subject: 'fleet:paper:USDC',
			checks: [
				{
					check: 'daily_loss',
					status: 'blocked',
					reason_code: 'DAILY_LOSS_LIMIT',
					blocker_class: 'latch',
					fleet_wide: true,
					detail: 'Daily-loss breaker is latched.',
					deployments: []
				}
			]
		});
		expect(fleetBanner(report([latched]))?.tone).toBe('warn');
	});

	it('never treats an unreadable fleet as clear', () => {
		const unread = report([]);
		unread.payload.entries.complete = false;
		unread.payload.entries.detail = 'Deployments could not be listed.';
		const banner = fleetBanner(unread);
		expect(banner?.title).toBe('Fleet entry readiness is unknown');
		expect(banner?.systemic).toContain('Deployments could not be listed.');
	});

	it('lists systemic decision blockers even when entries are admissible', () => {
		const banner = fleetBanner(
			report([scope()], {
				systemic: [
					{
						kind: 'sizing_skips',
						outcome: 'skipped',
						reason_code: 'NOTIONAL_BELOW_MINIMUM',
						rows: 4,
						deployments: 2,
						deployment_ids: [],
						detail: 'NOTIONAL_BELOW_MINIMUM skipped 4 matched signal(s) on 2 bot(s).'
					}
				]
			})
		);
		expect(banner?.tone).toBe('warn');
		expect(banner?.title).toBe('Systemic entry blockers in the last 24 h');
		expect(banner?.systemic[0]).toContain('NOTIONAL_BELOW_MINIMUM');
	});
});

describe('fleetBanner capacity', () => {
	it('shows a fully invested live fleet as a warning, not danger', () => {
		const full = scope({
			entries_admissible: 'blocked',
			reason_codes: ['MAX_OPEN_POSITIONS'],
			checks: [
				{
					check: 'open_position_slots',
					status: 'blocked',
					reason_code: 'MAX_OPEN_POSITIONS',
					blocker_class: 'capacity',
					fleet_wide: true,
					detail: '8 open or pending positions use every one of the 8 concurrent slots.',
					deployments: []
				}
			]
		});
		const banner = fleetBanner(report([full]));
		expect(banner?.tone).toBe('warn');
		expect(banner?.scopes[0].clears).toContain('exit');
	});
});
