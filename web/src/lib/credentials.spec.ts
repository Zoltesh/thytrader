import { describe, expect, it, vi } from 'vitest';

import { fetchCoinbaseCredentialsStatus, setCoinbaseCredentials } from './credentials';

const status = {
	provider: 'coinbase' as const,
	configured: true,
	persisted: true,
	env_file_writable: true,
	api_hot_reloaded: true,
	workers_require_restart: true,
	workers_restart_detail: 'restart workers'
};

describe('credentials client', () => {
	it('GETs presence flags without sending a body', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => status
		});
		vi.stubGlobal('fetch', fetchMock);
		await expect(fetchCoinbaseCredentialsStatus()).resolves.toEqual(status);
		expect(fetchMock).toHaveBeenCalledWith('/api/v1/credentials/coinbase', {
			headers: { Accept: 'application/json' }
		});
		vi.unstubAllGlobals();
	});

	it('PUTs key name and private key then returns status only', async () => {
		const fetchMock = vi
			.fn()
			.mockImplementation(async (path: string) =>
				path === '/api/v1/security/session'
					? { ok: true, json: async () => ({ csrf_token: 'credential-test-csrf' }) }
					: { ok: true, json: async () => status }
			);
		vi.stubGlobal('fetch', fetchMock);
		await expect(
			setCoinbaseCredentials({
				api_key_name: 'organizations/example/apiKeys/example',
				private_key: 'SYNTHETIC-DO-NOT-ECHO'
			})
		).resolves.toEqual(status);
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/security/session');
		const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
		expect(init.method).toBe('PUT');
		expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('credential-test-csrf');
		expect(init.body).toContain('SYNTHETIC-DO-NOT-ECHO');
		vi.unstubAllGlobals();
	});

	it('DELETE clears without echoing secrets from a generic 422', async () => {
		vi.resetModules();
		const fetchMock = vi
			.fn()
			.mockImplementation(async (path: string) =>
				path === '/api/v1/security/session'
					? { ok: true, json: async () => ({ csrf_token: 'delete-test-csrf' }) }
					: { ok: false, json: async () => ({ detail: 'Invalid Coinbase credentials payload.' }) }
			);
		vi.stubGlobal('fetch', fetchMock);
		const { clearCoinbaseCredentials: clearWithFreshSession } = await import('./credentials');
		await expect(clearWithFreshSession()).rejects.toThrow('Invalid Coinbase credentials payload.');
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/security/session');
		const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
		expect(init.method).toBe('DELETE');
		expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('delete-test-csrf');
		vi.unstubAllGlobals();
	});
});
