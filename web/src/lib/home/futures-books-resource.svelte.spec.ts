/** Real resource transitions retain last-good rows across domain and HTTP failures. */
import { afterEach, expect, it, vi } from 'vitest';
import type { FuturesBooksReport } from '../futures-book';
import { fetchFuturesBooks } from '../futures-book-api';
import { FuturesBooksResource, booksReportFailure } from './futures-books-resource.svelte';

const empty: FuturesBooksReport = {
	report_kind: 'futures_books',
	overall_status: 'healthy',
	components: [],
	payload: {
		paper_capital_usd: null,
		committed_paper_cash_usd: '0',
		futures_policy_set: false,
		books: [],
		collateral_note: 'USD only',
		live_supported: true
	}
};
const failed: FuturesBooksReport = {
	...empty,
	overall_status: 'failed',
	components: [
		{
			name: 'futures_books',
			status: 'failed',
			reason_code: 'STORE_UNAVAILABLE',
			detail: 'Storage unavailable'
		}
	]
};

afterEach(() => vi.unstubAllGlobals());

it('never confuses failed or partial empty inventory with healthy empty inventory', async () => {
	let report = failed;
	const resource = new FuturesBooksResource(async () => report);
	await resource.reload();
	expect(resource.state.status).toBe('error');
	expect(booksReportFailure(empty)).toBeNull();
	report = { ...failed, overall_status: 'degraded' };
	await resource.reload();
	expect(resource.state.status).toBe('error');
	report = empty;
	await resource.reload();
	expect(resource.state.status).toBe('ready');
	report = failed;
	await resource.reload();
	expect(resource.state.status).toBe('error');
	expect(resource.state.data).toEqual(empty);
});

it('preserves a non-2xx report as explicitly failed partial data', async () => {
	vi.stubGlobal('fetch', async () => new Response(JSON.stringify(empty), { status: 503 }));
	const report = await fetchFuturesBooks();
	expect(report.overall_status).toBe('failed');
	expect(report.payload).toEqual(empty.payload);
	expect(booksReportFailure(report)).toContain('HTTP_READ_FAILED');
});

it('rejects transport errors without a usable report', async () => {
	vi.stubGlobal(
		'fetch',
		async () => new Response(JSON.stringify({ detail: 'Unavailable' }), { status: 503 })
	);
	await expect(fetchFuturesBooks()).rejects.toThrow('unavailable');
});

it.each(['domain', 'transport'] as const)(
	'retains unresolved %s evidence while a retry is pending',
	async (kind) => {
		let finish!: (report: FuturesBooksReport) => void;
		let retry = false;
		const resource = new FuturesBooksResource(async () => {
			if (retry)
				return new Promise<FuturesBooksReport>((resolve) => {
					finish = resolve;
				});
			if (kind === 'transport') throw new Error('Synthetic failure');
			return failed;
		});
		await resource.reload();
		const evidence = resource.state;
		expect(evidence.status).toBe('error');
		retry = true;
		const pending = resource.reload();
		expect(resource.state).toEqual(evidence);
		finish(empty);
		await pending;
		expect(resource.state.status).toBe('ready');
	}
);
