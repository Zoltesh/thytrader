import { describe, expect, it, vi } from 'vitest';
import { listAllDeployments, type Deployment } from './deployments';
import { executeFleet } from './fleet-control';

vi.mock('$lib/security', () => ({
	ensureBrowserCsrfSession: async () => {},
	mutationHeaders: () => ({})
}));

const fence = {
	as_of: '2026-10-06T00:00:00Z',
	total: 201,
	order: 'created_at_desc_id_desc',
	fingerprint: 'unchanged'
};

function page(index: number, anomaly: string): object {
	if (index === 1)
		return {
			...fence,
			deployments: Array.from({ length: 200 }, (_, row) => ({ id: `first-${row}` })),
			returned: 200,
			has_more: true,
			next_cursor: 'continuation'
		};
	const result = {
		...fence,
		deployments: [{ id: anomaly === 'duplicate' ? 'first-0' : 'last' }],
		returned: 1,
		has_more: false,
		next_cursor: null as string | null
	};
	if (anomaly === 'deletion') {
		result.total = 200;
		result.fingerprint = 'deleted';
	}
	if (anomaly === 'reclassification') result.fingerprint = 'reclassified';
	if (anomaly === 'repeated_cursor') {
		result.has_more = true;
		result.next_cursor = 'continuation';
	}
	return result;
}

describe('truthful complete inventory', () => {
	for (const anomaly of ['deletion', 'reclassification', 'duplicate', 'repeated_cursor']) {
		it(`rejects ${anomaly} without publishing a partial inventory`, async () => {
			const original = globalThis.fetch;
			let calls = 0;
			const published: Deployment[][] = [];
			globalThis.fetch = (async (input: RequestInfo | URL) => {
				const url = new URL(String(input), 'http://local');
				calls += 1;
				if (calls === 2) expect(url.searchParams.get('cursor')).toBe('continuation');
				return new Response(JSON.stringify(page(calls, anomaly)), { status: 200 });
			}) as typeof fetch;
			try {
				await expect(listAllDeployments((rows) => published.push(rows))).rejects.toThrow(
					/incomplete/
				);
				expect(published).toEqual([]);
			} finally {
				globalThis.fetch = original;
			}
		});
	}
});

describe('fleet preview revision propagation', () => {
	it('sends the exact confirmed latch revisions and preserves live/confirm gates', async () => {
		const original = globalThis.fetch;
		let submitted: unknown;
		globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
			submitted = JSON.parse(String(init?.body));
			return new Response('{}', { status: 200 });
		}) as typeof fetch;
		try {
			await executeFleet('rearm', {
				mode: 'all',
				idempotencyKey: 'same-request',
				expectedTargets: [],
				liveAcknowledged: true,
				allowEmptyScope: true,
				expectedInhibition: { paper_revision: 9, live_revision: 11 }
			});
			expect(submitted).toMatchObject({
				confirm: true,
				i_understand_live: true,
				idempotency_key: 'same-request',
				expected_inhibition: { paper_revision: 9, live_revision: 11 }
			});
		} finally {
			globalThis.fetch = original;
		}
	});
});
