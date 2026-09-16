// Deliberate production entrypoint for the adapter-node build. Wraps the compiled
// SvelteKit request handler with the same `/api` reverse proxy the Vite dev server
// provides in development, so browser requests to `/api/...` keep working against a
// built, production-served frontend instead of the dev server (audit F26 / ADR 0063).
import { createServer } from 'node:http';
import { handler } from './build/handler.js';

const PORT = Number(process.env.PORT ?? 5175);
const HOST = process.env.HOST ?? '0.0.0.0';
const API_PROXY_TARGET = process.env.THYTRADER_API_PROXY_TARGET ?? 'http://127.0.0.1:8200';

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
		if (value === undefined || name.toLowerCase() === 'host') {
			continue;
		}
		headers.set(name, Array.isArray(value) ? value.join(', ') : value);
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
