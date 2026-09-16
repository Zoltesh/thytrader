import { describe, expect, it, vi } from 'vitest';

import {
	clearCoinbaseCredentials,
	fetchCoinbaseCredentialsStatus,
	setCoinbaseCredentials
} from './credentials';

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
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => status
		});
		vi.stubGlobal('fetch', fetchMock);
		await expect(
			setCoinbaseCredentials({
				api_key_name: 'organizations/example/apiKeys/example',
				private_key: 'SYNTHETIC-DO-NOT-ECHO'
			})
		).resolves.toEqual(status);
		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(init.method).toBe('PUT');
		expect(init.body).toContain('SYNTHETIC-DO-NOT-ECHO');
		vi.unstubAllGlobals();
	});

	it('DELETE clears without echoing secrets from a generic 422', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: false,
			json: async () => ({ detail: 'Invalid Coinbase credentials payload.' })
		});
		vi.stubGlobal('fetch', fetchMock);
		await expect(clearCoinbaseCredentials()).rejects.toThrow(
			'Invalid Coinbase credentials payload.'
		);
		vi.unstubAllGlobals();
	});
});
