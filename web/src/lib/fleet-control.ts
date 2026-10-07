import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';

export type FleetMode = 'paper' | 'live' | 'all';
export type FleetAction = 'disarm' | 'managed_stop' | 'flatten' | 'rearm';

export type FleetInhibition = {
	paper_inhibited: boolean;
	live_inhibited: boolean;
	paper_revision: number;
	live_revision: number;
	updated_at: string | null;
};

export type FleetResidualPosition = {
	product_id: string;
	side: string;
	quantity: string;
};

export type FleetTarget = {
	deployment_id: string;
	revision: number;
	mode: string;
	status: string;
	lifecycle_command: string;
	product_id: string;
	positions: FleetResidualPosition[] | null;
	positions_known: boolean;
	effect: string;
};

export type FleetPreview = {
	action: FleetAction;
	mode: FleetMode;
	effect: string;
	cancels_entries: boolean;
	flattens: boolean;
	pauses: boolean;
	requires_live_acknowledgement: boolean;
	inhibition: FleetInhibition;
	targets: FleetTarget[];
	as_of: string;
};

export type FleetTargetResult = {
	deployment_id: string;
	expected_revision: number | null;
	status: string;
	detail: string;
	venue_effect: string;
};

export type FleetOperation = {
	id: string;
	idempotency_key: string;
	action: string;
	mode: FleetMode;
	status: string;
	targets: FleetTargetResult[];
	inhibition: FleetInhibition;
	live_acknowledged: boolean;
	audit_recorded: boolean;
	note: string;
	atomic_venue_transaction: false;
};

const ACTION_PATH: Record<FleetAction, string> = {
	disarm: 'disarm',
	managed_stop: 'stop',
	flatten: 'flatten',
	rearm: 'rearm'
};

export function fleetEffect(action: FleetAction): string {
	switch (action) {
		case 'disarm':
			return 'Inhibits new starts and entries. Does not pause, cancel, or flatten.';
		case 'managed_stop':
			return 'Records managed shutdown. Cancels risk-increasing entries and keeps protection. Not a flatten.';
		case 'flatten':
			return 'Records an explicit flatten. Marketable exits then cancel remainders. Not a pause.';
		case 'rearm':
			return 'Clears entry inhibition. Does not resume books. Live scope needs the live acknowledgement.';
	}
}

export function fleetNeedsLiveAck(action: FleetAction, mode: FleetMode): boolean {
	const includesLive = mode === 'live' || mode === 'all';
	return includesLive && (action === 'rearm' || action === 'flatten');
}

export function residualLabel(target: FleetTarget): string {
	if (!target.positions_known || target.positions === null) {
		return 'Residual positions unknown';
	}
	if (target.positions.length === 0) return 'No open residual position';
	return target.positions
		.map((position) => `${position.product_id} ${position.side} ${position.quantity}`)
		.join(', ');
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const method = init?.method?.toUpperCase() ?? 'GET';
	if (method !== 'GET' && method !== 'HEAD') await ensureBrowserCsrfSession();
	const response = await fetch(path, {
		...init,
		headers: {
			'content-type': 'application/json',
			...(init?.headers ?? {}),
			...mutationHeaders()
		}
	});
	if (!response.ok) {
		let message = `Request failed (${response.status})`;
		try {
			const body = (await response.json()) as { detail?: unknown };
			if (typeof body.detail === 'string') message = body.detail;
		} catch {
			/* keep fallback */
		}
		throw new Error(message);
	}
	return (await response.json()) as T;
}

export function fetchFleetInhibition(): Promise<FleetInhibition> {
	return request('/api/v1/fleet-control');
}

export function previewFleet(action: FleetAction, mode: FleetMode): Promise<FleetPreview> {
	const params = new URLSearchParams({ action, mode });
	return request(`/api/v1/fleet-control/preview?${params.toString()}`);
}

export function executeFleet(
	action: FleetAction,
	input: {
		mode: FleetMode;
		idempotencyKey: string;
		expectedTargets: { deployment_id: string; revision: number }[];
		liveAcknowledged: boolean;
		allowEmptyScope: boolean;
		expectedInhibition: { paper_revision?: number; live_revision?: number };
	}
): Promise<FleetOperation> {
	return request(`/api/v1/fleet-control/${ACTION_PATH[action]}`, {
		method: 'POST',
		body: JSON.stringify({
			mode: input.mode,
			confirm: true,
			idempotency_key: input.idempotencyKey,
			i_understand_live: input.liveAcknowledged,
			expected_targets: input.expectedTargets,
			allow_empty_scope: input.allowEmptyScope,
			expected_inhibition: input.expectedInhibition
		})
	});
}
