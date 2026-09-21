import { describe, expect, it } from 'vitest';

import type { Deployment } from './deployments';
import {
	lifecycleContractNote,
	lifecycleContractOf,
	lifecycleControlsAvailable
} from './lifecycle-contract';

function baseDeployment(overrides: Partial<Deployment> = {}): Deployment {
	return {
		id: '01a0ad72-0000-0000-0000-000000000000',
		strategy_fingerprint: 'sha256:abc',
		strategy_id: '01a0ad42-0000-0000-0000-000000000000',
		kind: 'strategy',
		timeframe: '1h',
		product_id: 'UNI-USD',
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '10000',
		paper_starting_cash: '10000',
		last_evaluated_bar: '2026-09-21T20:00:00+00:00',
		last_signal: null,
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 0,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 12,
		worker_lease_held: true,
		created_at: '2026-09-20T00:00:00+00:00',
		updated_at: '2026-09-21T20:00:00+00:00',
		position: null,
		orders: [],
		fills: [],
		...overrides
	};
}

describe('lifecycle contract gating', () => {
	it('allows controls when the full contract is present', () => {
		expect(lifecycleControlsAvailable(baseDeployment())).toBe(true);
		expect(lifecycleContractOf(baseDeployment()).state).toBe('complete');
	});

	it('forbids controls when a boolean latch is missing', () => {
		const deployment = baseDeployment();
		const { daily_loss_latched: _omitted, ...rest } = deployment;
		const partial = rest as Deployment;
		const contract = lifecycleContractOf(partial);
		expect(contract.state).toBe('incomplete');
		expect(contract.missing).toEqual(['daily_loss_latched']);
		expect(lifecycleControlsAvailable(partial)).toBe(false);
	});

	it('forbids controls when revision is missing and never infers 0', () => {
		const deployment = baseDeployment();
		const { revision: _omitted, ...rest } = deployment;
		const partial = rest as Deployment;
		const contract = lifecycleContractOf(partial);
		expect(contract.missing).toEqual(['revision']);
		expect(lifecycleControlsAvailable(partial)).toBe(false);
	});

	it('treats null worker_lease_held as missing, not false', () => {
		const partial = baseDeployment({
			worker_lease_held: null
		} as unknown as Partial<Deployment>);
		const contract = lifecycleContractOf(partial);
		expect(contract.missing).toEqual(['worker_lease_held']);
		expect(lifecycleControlsAvailable(partial)).toBe(false);
	});

	it('flags malformed lifecycle_command values', () => {
		const bad = baseDeployment({ lifecycle_command: 'paused' } as Partial<Deployment>);
		const contract = lifecycleContractOf(bad);
		expect(contract.state).toBe('malformed');
		expect(contract.malformed).toEqual(['lifecycle_command']);
		expect(lifecycleControlsAvailable(bad)).toBe(false);
	});

	it('flags non-boolean latch values as malformed', () => {
		const bad = baseDeployment({
			daily_loss_latched: 'false'
		} as unknown as Partial<Deployment>);
		const contract = lifecycleContractOf(bad);
		expect(contract.state).toBe('malformed');
		expect(lifecycleControlsAvailable(bad)).toBe(false);
	});

	it('reports missing fields before malformed ones in the note', () => {
		const bad = baseDeployment({
			worker_lease_held: null,
			lifecycle_command: 'bogus'
		} as unknown as Partial<Deployment>);
		const note = lifecycleContractNote(bad);
		expect(note).toContain('incomplete');
		expect(note).toContain('lifecycle_command');
	});
});
