const DEFAULT_API_TARGET = 'http://127.0.0.1:8200';

const HOP_BY_HOP = new Set([
	'connection',
	'content-length',
	'host',
	'keep-alive',
	'proxy-authenticate',
	'proxy-authorization',
	'te',
	'trailer',
	'transfer-encoding',
	'upgrade'
]);

const RESPONSE_STRIPPED = new Set([...HOP_BY_HOP, 'content-encoding']);

export function apiTarget(value: string | undefined): string {
	const target = value === undefined || value === '' ? DEFAULT_API_TARGET : value;
	return target.endsWith('/') ? target.slice(0, -1) : target;
}

export function upstreamUrl(target: string | undefined, path: string, search: string): string {
	return `${apiTarget(target)}/api/${path}${search}`;
}

export function forwardedRequestHeaders(source: Headers): Headers {
	const headers = new Headers();
	for (const [name, value] of source) {
		if (!HOP_BY_HOP.has(name.toLowerCase())) {
			headers.set(name, value);
		}
	}
	return headers;
}

export function forwardedResponseHeaders(source: Headers): Headers {
	const headers = new Headers();
	for (const [name, value] of source) {
		if (!RESPONSE_STRIPPED.has(name.toLowerCase())) {
			headers.append(name, value);
		}
	}
	return headers;
}
