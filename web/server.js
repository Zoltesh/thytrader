// Deliberate production entrypoint for the adapter-node build. Wraps the compiled
// SvelteKit request handler with the same `/api` reverse proxy the Vite dev server
// provides in development, so browser requests to `/api/...` keep working against a
// built, production-served frontend instead of the dev server (audit F26 / ADR 0063).
// Like the dev proxy, this server injects the installation credential on proxied
// `/api` calls: the API's trust boundary (ADR 0061) requires installation auth on
// non-read calls such as CSRF session minting, and the browser never holds the
// token. The credential comes from `THYTRADER_INSTALLATION_TOKEN` (same name as the
// dev proxy uses) or, failing that, from the durable token file inside the
// credentials volume via `THYTRADER_INSTALLATION_TOKEN_FILE`. It stays server-side.
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { handler } from './build/handler.js';

const PORT = Number(process.env.PORT ?? 5175);
const HOST = process.env.HOST ?? '0.0.0.0';
const API_PROXY_TARGET = process.env.THYTRADER_API_PROXY_TARGET ?? 'http://127.0.0.1:8200';

/**
 * Resolve the installation credential without exposing it to the browser.
 *
 * @returns {string} The token, or an empty string when unavailable. An empty
 * string keeps proxy behavior identical to before (no Authorization header),
 * which the API boundary rejects for non-read calls.
 */
function resolveInstallationToken() {
	const fromEnv = process.env.THYTRADER_INSTALLATION_TOKEN?.trim();
	if (fromEnv) {
		return fromEnv;
	}
	const tokenFile = process.env.THYTRADER_INSTALLATION_TOKEN_FILE?.trim();
	if (tokenFile) {
		try {
			return readFileSync(tokenFile, 'utf8').trim();
		} catch {
			// Missing/unreadable file: fall through to no-token behavior.
		}
	}
	return '';
}

const INSTALLATION_TOKEN = resolveInstallationToken();
if (!INSTALLATION_TOKEN) {
	console.warn(
		'ThyTrader web: no installation credential available (THYTRADER_INSTALLATION_TOKEN or THYTRADER_INSTALLATION_TOKEN_FILE); proxied non-read API calls will be rejected by the trust boundary.'
	);
}

/**
 * Forward one `/api/...` request to the backend API and stream the response back.
 * @param {import('node:http').IncomingMessage} request Inbound HTTP request.
 * @param {import('node:http').ServerResponse} response Outbound HTTP response.
 * @returns {Promise<void>} Resolves once the proxied response has been fully written.
 */
async function proxyApiRequest(request, response) {
	const target = new URL(request.url ?? '/', API_PROXY_TARGET);
	const headers = new Headers();
	for (const [name, value] of Object.entries(request.headers)) {
		if (
			value === undefined ||
			name.toLowerCase() === 'host' ||
			name.toLowerCase() === 'authorization'
		) {
			continue;
		}
		headers.set(name, Array.isArray(value) ? value.join(', ') : value);
	}
	if (INSTALLATION_TOKEN) {
		headers.set('authorization', `Bearer ${INSTALLATION_TOKEN}`);
	}
	const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
	try {
		const upstream = await fetch(target, {
			method: request.method,
			headers,
			body: hasBody ? /** @type {ReadableStream} */ (request) : undefined,
			duplex: hasBody ? 'half' : undefined
		});
		response.statusCode = upstream.status;
		upstream.headers.forEach((value, name) => {
			if (name.toLowerCase() === 'transfer-encoding') {
				return;
			}
			response.setHeader(name, value);
		});
		if (!upstream.body) {
			response.end();
			return;
		}
		for await (const chunk of upstream.body) {
			response.write(chunk);
		}
		response.end();
	} catch (error) {
		response.statusCode = 502;
		response.setHeader('content-type', 'application/json');
		response.end(
			JSON.stringify({
				error: 'api_proxy_unreachable',
				detail: error instanceof Error ? error.message : String(error)
			})
		);
	}
}

const server = createServer((request, response) => {
	if (request.url && request.url.startsWith('/api/')) {
		void proxyApiRequest(request, response);
		return;
	}
	handler(request, response);
});

server.listen(PORT, HOST, () => {
	console.log(
		`ThyTrader web listening on http://${HOST}:${PORT}, proxying /api to ${API_PROXY_TARGET}`
	);
});
