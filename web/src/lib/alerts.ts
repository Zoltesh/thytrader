/**
 * Typed client for the durable operator safety-alert feed (ADR 0115).
 */

export interface AlertDeliveryView {
	provider: string;
	status: string;
	attempts: number;
	detail: string;
}

export interface AlertItem {
	id: string;
	code: string;
	severity: 'info' | 'warning' | 'critical';
	scope: 'deployment' | 'worker' | 'fleet';
	subject: string;
	deployment_id: string | null;
	product_id: string | null;
	detail: string;
	first_seen_at: string;
	last_seen_at: string;
	occurrences: number;
	resolved_at: string | null;
	delivery: AlertDeliveryView;
}

export interface AlertsPayload {
	storage: 'available' | 'unavailable';
	delivery_enabled: boolean;
	delivery_warning: string | null;
	open_alerts: AlertItem[];
	resolved_alerts: AlertItem[];
	open_total: number;
	open_critical: number;
	open_warning: number;
}

export interface AlertsReport {
	overall_status: 'healthy' | 'degraded' | 'failed';
	recommended_next_action: string;
	payload: AlertsPayload;
}

/** Banner text when external delivery is off. Null when a provider is configured. */
export function deliveryDisabledWarning(payload: AlertsPayload): string | null {
	if (payload.delivery_enabled) {
		return null;
	}
	return (
		payload.delivery_warning ??
		'notify_provider=none: external delivery is disabled. Alerts stay in the local feed.'
	);
}

export async function fetchOperatorAlerts(): Promise<AlertsReport> {
	const response = await fetch('/api/v1/operator/alerts', {
		headers: { Accept: 'application/json' }
	});
	if (!response.ok) {
		throw new Error('Safety alert feed is unavailable.');
	}
	return (await response.json()) as AlertsReport;
}
