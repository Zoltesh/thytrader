import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => vi.resetModules());
afterEach(() => vi.unstubAllGlobals());

describe('browser CSRF bootstrap', () => {
	it('shares one security session between concurrent mutations', async () => {
		let finish: ((value: object) => void) | undefined;
		const fetchMock = vi.fn().mockImplementation(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				})
		);
		vi.stubGlobal('fetch', fetchMock);
		const { ensureBrowserCsrfSession, mutationHeaders } = await import('./security');
		const first = ensureBrowserCsrfSession();
		const second = ensureBrowserCsrfSession();
		expect(fetchMock).toHaveBeenCalledTimes(1);
		finish?.({ ok: true, json: async () => ({ csrf_token: 'shared-token' }) });
		await Promise.all([first, second]);
		expect(mutationHeaders()).toEqual({ 'X-CSRF-Token': 'shared-token' });
		await ensureBrowserCsrfSession();
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it('rejects a failed bootstrap and permits a later retry', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce({ ok: false })
			.mockResolvedValueOnce({ ok: true, json: async () => ({ csrf_token: 'retry-token' }) });
		vi.stubGlobal('fetch', fetchMock);
		const { ensureBrowserCsrfSession, mutationHeaders } = await import('./security');
		await expect(ensureBrowserCsrfSession()).rejects.toThrow(
			'Could not establish a browser security session.'
		);
		await ensureBrowserCsrfSession();
		expect(fetchMock).toHaveBeenCalledTimes(2);
		expect(mutationHeaders()).toEqual({ 'X-CSRF-Token': 'retry-token' });
	});
});
