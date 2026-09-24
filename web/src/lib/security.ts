/**
 * Browser CSRF bootstrap for the loopback trust boundary.
 * Installation auth is injected by the dev proxy or Compose web service env.
 */

let csrfToken: string | null = null;
let sessionRequest: Promise<void> | null = null;

export async function ensureBrowserCsrfSession(): Promise<void> {
	if (csrfToken !== null) {
		return;
	}
	sessionRequest ??= (async () => {
		const response = await fetch('/api/v1/security/session', {
			headers: { Accept: 'application/json' }
		});
		if (!response.ok) {
			throw new Error('Could not establish a browser security session.');
		}
		const payload = (await response.json()) as { csrf_token: string };
		csrfToken = payload.csrf_token;
	})();
	try {
		await sessionRequest;
	} finally {
		sessionRequest = null;
	}
}

export function mutationHeaders(): Record<string, string> {
	if (csrfToken === null) {
		return {};
	}
	return { 'X-CSRF-Token': csrfToken };
}
