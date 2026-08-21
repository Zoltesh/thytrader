/**
 * Typed API client for append-only operational audit trail events.
 */

export interface AuditEventItem {
	id: string;
	occurred_at: string;
	category: 'connection' | 'snapshot' | 'worker_error' | 'market_data' | 'websocket';
	action: string;
	outcome: 'success' | 'failure' | 'info';
	detail: string;
	provider: string | null;
	product_id: string | null;
	recorded_at: string;
}

export interface AuditEventListResponse {
	events: AuditEventItem[];
}

export interface AuditPersistenceErrorResponse {
	detail?: {
		code: string;
		message: string;
	};
}

export async function fetchAuditEvents(limit = 50): Promise<AuditEventItem[]> {
	const response = await fetch(`/api/v1/audit-events?limit=${limit}`, {
		headers: { Accept: 'application/json' }
	});

	if (!response.ok) {
		let message = 'Audit event storage is unavailable.';
		try {
			const errorBody = (await response.json()) as AuditPersistenceErrorResponse;
			if (errorBody.detail?.message) {
				message = errorBody.detail.message;
			}
		} catch {
			// fallback to default message
		}
		throw new Error(message);
	}

	const payload = (await response.json()) as AuditEventListResponse;
	return payload.events;
}
