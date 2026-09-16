import { describe, expect, it } from 'vitest';
import {
	apiTarget,
	forwardedRequestHeaders,
	forwardedResponseHeaders,
	upstreamUrl
} from '$lib/server/api-proxy';

describe('apiTarget', () => {
	it('falls back to the loopback API when no target is configured', () => {
		expect(apiTarget(undefined)).toBe('http://127.0.0.1:8200');
		expect(apiTarget('')).toBe('http://127.0.0.1:8200');
	});

	it('drops one trailing slash so paths never double up', () => {
		expect(apiTarget('http://api:8200/')).toBe('http://api:8200');
	});
});

describe('upstreamUrl', () => {
	it('rebuilds the versioned API path and query string', () => {
		expect(upstreamUrl('http://api:8200', 'v1/deployments', '?mode=paper')).toBe(
			'http://api:8200/api/v1/deployments?mode=paper'
		);
	});

	it('keeps an empty path and query usable', () => {
		expect(upstreamUrl(undefined, '', '')).toBe('http://127.0.0.1:8200/api/');
	});
});

describe('forwardedRequestHeaders', () => {
	it('drops hop-by-hop headers and keeps operator context', () => {
		const source = new Headers({
			host: 'localhost:5175',
			connection: 'keep-alive',
			'content-length': '12',
			origin: 'http://127.0.0.1:5175',
			'content-type': 'application/json'
		});
		const forwarded = forwardedRequestHeaders(source);
		expect(forwarded.get('host')).toBeNull();
		expect(forwarded.get('connection')).toBeNull();
		expect(forwarded.get('content-length')).toBeNull();
		expect(forwarded.get('origin')).toBe('http://127.0.0.1:5175');
		expect(forwarded.get('content-type')).toBe('application/json');
	});
});

describe('forwardedResponseHeaders', () => {
	it('drops already-decoded content encoding and hop-by-hop headers', () => {
		const source = new Headers({
			'content-encoding': 'gzip',
			'transfer-encoding': 'chunked',
			'content-type': 'application/json',
			'cache-control': 'no-store'
		});
		const forwarded = forwardedResponseHeaders(source);
		expect(forwarded.get('content-encoding')).toBeNull();
		expect(forwarded.get('transfer-encoding')).toBeNull();
		expect(forwarded.get('content-type')).toBe('application/json');
		expect(forwarded.get('cache-control')).toBe('no-store');
	});
});
