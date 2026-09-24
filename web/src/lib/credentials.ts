import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';

/**
 * Write-only Coinbase Advanced Trade credential client.
 *
 * GET never returns secrets. Callers must not log request bodies or responses
 * beyond presence flags.
 */

export type CoinbaseCredentialsStatus = {
	provider: 'coinbase';
	configured: boolean;
	persisted: boolean;
	env_file_writable: boolean;
	api_hot_reloaded: boolean;
	workers_require_restart: boolean;
	workers_restart_detail: string;
};

async function parseStatus(
	response: Response,
	fallback: string
): Promise<CoinbaseCredentialsStatus> {
	if (!response.ok) {
		let message = fallback;
		try {
			const body = (await response.json()) as { detail?: unknown };
			if (typeof body.detail === 'string') message = body.detail;
		} catch {
			/* keep fallback — never echo a raw body that might contain secrets */
		}
		throw new Error(message);
	}
	return (await response.json()) as CoinbaseCredentialsStatus;
}

export async function fetchCoinbaseCredentialsStatus(): Promise<CoinbaseCredentialsStatus> {
	const response = await fetch('/api/v1/credentials/coinbase', {
		headers: { Accept: 'application/json' }
	});
	return parseStatus(response, 'Could not load Coinbase credential status.');
}

export async function setCoinbaseCredentials(input: {
	api_key_name: string;
	private_key: string;
}): Promise<CoinbaseCredentialsStatus> {
	await ensureBrowserCsrfSession();
	const response = await fetch('/api/v1/credentials/coinbase', {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			'content-type': 'application/json',
			...mutationHeaders()
		},
		body: JSON.stringify(input)
	});
	return parseStatus(response, 'Could not set Coinbase credentials.');
}

export async function clearCoinbaseCredentials(): Promise<CoinbaseCredentialsStatus> {
	await ensureBrowserCsrfSession();
	const response = await fetch('/api/v1/credentials/coinbase', {
		method: 'DELETE',
		headers: { Accept: 'application/json', ...mutationHeaders() }
	});
	return parseStatus(response, 'Could not clear Coinbase credentials.');
}
