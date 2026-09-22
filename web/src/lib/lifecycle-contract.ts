/**
 * Runtime lifecycle contract gating for deployment controls.
 *
 * ThyTrader's lifecycle rule: the UI may only show start/pause/resume/stop
 * controls when the backend payload carries the full lifecycle contract —
 * `lifecycle_command`, both breaker latches, `revision`, and
 * `worker_lease_held`. If required fields are missing or malformed, the
 * deployment is shown as read-only inventory: no lifecycle actions, no
 * inferred defaults. A missing `false` is not `false`; revision `0` is not
 * inferred when revision is absent.
 */
import type { Deployment } from './deployments';

export type LifecycleContractState = 'complete' | 'incomplete' | 'malformed';

export type LifecycleContract = {
	state: LifecycleContractState;
	/** Fields actually present and valid in the payload (all ten when complete). */
	present: string[];
	/** Contract fields missing from the payload. */
	missing: string[];
	/** Fields present with a value of the wrong type. */
	malformed: string[];
};

const STRING_FIELDS = ['lifecycle_command'] as const;
const BOOLEAN_FIELDS = ['daily_loss_latched', 'drawdown_latched', 'worker_lease_held'] as const;
const NUMBER_FIELDS = ['revision'] as const;

const KNOWN_LIFECYCLE_COMMANDS: readonly string[] = [
	'none',
	'stop_new_entries',
	'flatten',
	'managed_shutdown'
];

function isBool(value: unknown): value is boolean {
	return typeof value === 'boolean';
}

function isFiniteNumber(value: unknown): value is number {
	return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Inspect one deployment payload against the lifecycle contract.
 *
 * `incomplete` means fields are absent; `malformed` means a field is present
 * with the wrong type or an unrecognized `lifecycle_command` value. Both
 * states forbid lifecycle controls.
 */
export function lifecycleContractOf(deployment: Deployment): LifecycleContract {
	const record = deployment as unknown as Record<string, unknown>;
	const present: string[] = [];
	const missing: string[] = [];
	const malformed: string[] = [];

	for (const field of STRING_FIELDS) {
		const value = record[field];
		if (value === undefined || value === null) {
			missing.push(field);
		} else if (typeof value !== 'string') {
			malformed.push(field);
		} else if (!KNOWN_LIFECYCLE_COMMANDS.includes(value)) {
			malformed.push(field);
		} else {
			present.push(field);
		}
	}
	for (const field of BOOLEAN_FIELDS) {
		const value = record[field];
		if (value === undefined || value === null) {
			missing.push(field);
		} else if (!isBool(value)) {
			malformed.push(field);
		} else {
			present.push(field);
		}
	}
	for (const field of NUMBER_FIELDS) {
		const value = record[field];
		if (value === undefined || value === null) {
			missing.push(field);
		} else if (!isFiniteNumber(value)) {
			malformed.push(field);
		} else {
			present.push(field);
		}
	}
	const state: LifecycleContractState =
		malformed.length > 0 ? 'malformed' : missing.length > 0 ? 'incomplete' : 'complete';
	return { state, present, missing, malformed };
}

/**
 * Whether lifecycle controls may render for this deployment.
 *
 * Deliberately strict: any incompleteness or malformation returns false.
 */
export function lifecycleControlsAvailable(deployment: Deployment): boolean {
	return lifecycleContractOf(deployment).state === 'complete';
}

/**
 * Human explanation for why controls are unavailable, for the UI read-only note.
 */
export function lifecycleContractNote(deployment: Deployment): string | null {
	const contract = lifecycleContractOf(deployment);
	if (contract.state === 'complete') {
		return null;
	}
	const detail =
		contract.state === 'malformed'
			? `invalid values for: ${contract.malformed.join(', ')}`
			: `missing from the response: ${contract.missing.join(', ')}`;
	return `Lifecycle controls are unavailable because the deployment contract is incomplete (${detail}). Values are shown read-only; nothing is inferred.`;
}

/** Whether the deployment snapshot is safe to show as the canonical state. */
export function deploymentSnapshotComplete(deployment: Deployment): boolean {
	return lifecycleContractOf(deployment).state === 'complete';
}
