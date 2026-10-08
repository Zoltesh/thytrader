/** JSON request helper of the deployment client: CSRF session, mutation headers, error detail. */
import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const method = init?.method?.toUpperCase() ?? 'GET';
	if (method !== 'GET' && method !== 'HEAD') {
		await ensureBrowserCsrfSession();
	}
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
