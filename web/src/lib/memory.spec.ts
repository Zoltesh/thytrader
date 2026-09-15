import { describe, expect, it, vi } from 'vitest';

import { fetchMemoryStatus } from './memory';

describe('memory client', () => {
	it('loads redacted memory status without posting mutations', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({
				schema_version: 'thytrader-experiential-memory-v1',
				counts: { journals: 1, sentiment: 0, patterns: 0, notifications: 0 },
				notify_provider: 'none',
				notify_webhook_configured: false,
				notify_enabled: false,
				storage: 'available'
			})
		});
		vi.stubGlobal('fetch', fetchMock);
		const status = await fetchMemoryStatus();
		expect(status.notify_provider).toBe('none');
		expect(status.notify_webhook_configured).toBe(false);
		expect(fetchMock).toHaveBeenCalledWith('/api/v1/memory', {
			headers: { Accept: 'application/json' }
		});
		vi.unstubAllGlobals();
	});
});
