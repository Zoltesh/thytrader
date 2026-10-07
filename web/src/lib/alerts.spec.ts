import { describe, expect, it } from 'vitest';

import { deliveryDisabledWarning, type AlertsPayload } from './alerts';

function payload(deliveryEnabled: boolean, warning: string | null): AlertsPayload {
	return {
		storage: 'available',
		delivery_enabled: deliveryEnabled,
		delivery_warning: warning,
		open_alerts: [],
		resolved_alerts: [],
		open_total: 0,
		open_critical: 0,
		open_warning: 0
	};
}

describe('deliveryDisabledWarning', () => {
	it('surfaces the explicit local-only warning when delivery is off', () => {
		expect(deliveryDisabledWarning(payload(false, 'notify_provider=none: kept locally'))).toBe(
			'notify_provider=none: kept locally'
		);
	});

	it('does not invent a warning when a provider is configured', () => {
		expect(deliveryDisabledWarning(payload(true, null))).toBeNull();
	});
});
