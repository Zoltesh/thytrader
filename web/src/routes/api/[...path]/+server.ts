import { env } from '$env/dynamic/private';
import {
	forwardedRequestHeaders,
	forwardedResponseHeaders,
	upstreamUrl
} from '$lib/server/api-proxy';
import type { RequestHandler } from './$types';

// Vite's dev server proxies /api before SvelteKit routing, so this handler only
// serves the adapter-node production build documented in docs/user/deployment.md.
const proxy: RequestHandler = async ({ request, params, url }) => {
	const upstream = upstreamUrl(env.THYTRADER_API_PROXY_TARGET, params.path ?? '', url.search);
	const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
	let response: Response;
	try {
		response = await fetch(upstream, {
			method: request.method,
			headers: forwardedRequestHeaders(request.headers),
			body: hasBody ? await request.arrayBuffer() : undefined,
			redirect: 'manual'
		});
	} catch {
		return new Response(JSON.stringify({ detail: 'The ThyTrader API is unreachable.' }), {
			status: 502,
			headers: { 'content-type': 'application/json' }
		});
	}
	return new Response(response.body, {
		status: response.status,
		statusText: response.statusText,
		headers: forwardedResponseHeaders(response.headers)
	});
};

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
export const HEAD = proxy;
