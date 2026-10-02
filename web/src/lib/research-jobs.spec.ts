import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	isAcceptedResearchJob,
	researchJobFailureStatus,
	waitForResearchJob,
	type ResearchJobRecord
} from './research-jobs';
import { submitResearchStudy } from './research-studies';
import { StrategyApiError, submitBacktest } from './strategies';

const JOB_ID = '01985cf0-7b60-7000-8000-0000000000aa';
const RESULT = `sha256:${'d'.repeat(64)}`;
const RUN = `sha256:${'c'.repeat(64)}`;
const STUDY = `sha256:${'a'.repeat(64)}`;

function job(overrides: Partial<ResearchJobRecord>): ResearchJobRecord {
	return {
		job_id: JOB_ID,
		kind: 'backtest',
		status: 'running',
		progress_current: 0,
		progress_total: 1,
		...overrides
	};
}

function jsonResponse(body: unknown, status = 200) {
	return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const accepted = {
	job_id: JOB_ID,
	kind: 'backtest',
	status: 'queued',
	strategy_id: '01985cf0-7b60-7000-8000-000000000003',
	strategy_fingerprint: `sha256:${'b'.repeat(64)}`,
	bound_datasets: [],
	sync_wait_seconds: 25
};

afterEach(() => {
	vi.unstubAllGlobals();
});

/** Stub fetch: the CSRF session (fetched once per module) separately, then `responses` in order. */
function stubFetch(...responses: ReturnType<typeof jsonResponse>[]) {
	const queue = [...responses];
	const fetchMock = vi.fn(async (url: string) => {
		if (url === '/api/v1/security/session') return jsonResponse({ csrf_token: 'test-csrf' });
		const next = queue.shift();
		if (next === undefined) throw new Error(`unexpected fetch ${url}`);
		return next;
	});
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

describe('isAcceptedResearchJob', () => {
	it('tells a 202 job body from a finished result', () => {
		expect(isAcceptedResearchJob(accepted)).toBe(true);
		expect(isAcceptedResearchJob({ run_fingerprint: RUN, result_fingerprint: RESULT })).toBe(false);
		expect(isAcceptedResearchJob(null)).toBe(false);
	});
});

describe('waitForResearchJob', () => {
	it('polls with backoff until the job is terminal', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(jsonResponse(job({ status: 'queued' })))
			.mockResolvedValueOnce(jsonResponse(job({ status: 'running' })))
			.mockResolvedValueOnce(jsonResponse(job({ status: 'completed' })));
		const sleeps: number[] = [];
		const final = await waitForResearchJob(JOB_ID, {
			fetchImpl: fetchMock as unknown as typeof fetch,
			sleep: async (ms) => {
				sleeps.push(ms);
			}
		});
		expect(final.status).toBe('completed');
		expect(sleeps).toEqual([1000, 2000]);
		expect(fetchMock).toHaveBeenCalledWith(`/api/v1/research/jobs/${JOB_ID}`, {
			headers: { Accept: 'application/json' }
		});
	});

	it('gives up after the timeout while the worker keeps the job', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(job({ status: 'queued' })));
		await expect(
			waitForResearchJob(JOB_ID, {
				fetchImpl: fetchMock as unknown as typeof fetch,
				sleep: async () => {},
				timeoutMs: 1500
			})
		).rejects.toThrow(/still queued/);
	});
});

describe('researchJobFailureStatus', () => {
	it('maps caller-input codes to 422, cancellation to 409, and the rest to 503', () => {
		expect(
			researchJobFailureStatus(job({ status: 'failed', error_code: 'backtest_window_rejected' }))
		).toBe(422);
		expect(researchJobFailureStatus(job({ status: 'cancelled' }))).toBe(409);
		expect(
			researchJobFailureStatus(job({ status: 'failed', error_code: 'research_worker_lost' }))
		).toBe(503);
	});
});

describe('synchronous submits that fall back to HTTP 202', () => {
	it('submitBacktest polls the job and returns the same submission shape', async () => {
		stubFetch(
			jsonResponse(accepted, 202),
			jsonResponse(job({ status: 'completed', run_fingerprint: RUN, result_fingerprint: RESULT }))
		);
		const submission = await submitBacktest({
			strategy_id: accepted.strategy_id,
			dataset_fingerprint: `sha256:${'e'.repeat(64)}`,
			evaluation_start: '2026-01-01T00:00:00Z',
			evaluation_end: '2026-01-02T00:00:00Z',
			initial_quote_balance: '10000',
			maker_fee_rate: '0.001',
			taker_fee_rate: '0.002',
			fixed_slippage_bps: '10'
		});
		expect(submission).toEqual({
			run_fingerprint: RUN,
			result_fingerprint: RESULT,
			strategy_id: accepted.strategy_id,
			strategy_fingerprint: accepted.strategy_fingerprint
		});
	});

	it('submitBacktest raises the inline 422 for a rejected window', async () => {
		stubFetch(
			jsonResponse(accepted, 202),
			jsonResponse(
				job({
					status: 'failed',
					error_code: 'backtest_window_rejected',
					error_message: 'The evaluation window does not fit the dataset.'
				})
			)
		);
		const failure = await submitBacktest({
			strategy_id: accepted.strategy_id,
			dataset_fingerprint: `sha256:${'e'.repeat(64)}`,
			evaluation_start: '2026-01-01T00:00:00Z',
			evaluation_end: '2026-01-02T00:00:00Z',
			initial_quote_balance: '10000',
			maker_fee_rate: '0.001',
			taker_fee_rate: '0.002',
			fixed_slippage_bps: '10'
		}).catch((error: unknown) => error);
		expect(failure).toBeInstanceOf(StrategyApiError);
		expect((failure as StrategyApiError).status).toBe(422);
		expect((failure as StrategyApiError).code).toBe('backtest_window_rejected');
	});

	it('submitResearchStudy polls the job and loads the persisted study', async () => {
		const study = {
			study_fingerprint: STUDY,
			kind: 'oos_holdout',
			windows: [],
			aggregate: {
				oos_window_count: 1,
				oos_trade_count: 0,
				mean_oos_return_fraction: '0.01',
				mean_is_return_fraction: '0.02',
				is_oos_return_gap: '0.01'
			},
			warnings: []
		};
		const fetchMock = stubFetch(
			jsonResponse({ ...accepted, kind: 'study' }, 202),
			jsonResponse(job({ kind: 'study', status: 'completed', study_fingerprint: STUDY })),
			jsonResponse(study)
		);
		const finished = await submitResearchStudy({
			schema_version: 'thytrader-research-study-v1',
			kind: 'oos_holdout',
			evaluation_start: '2026-01-01T00:00:00Z',
			evaluation_end: '2026-01-11T00:00:00Z',
			initial_quote_balance: '10000',
			maker_fee_rate: '0.001',
			taker_fee_rate: '0.002',
			fixed_slippage_bps: '10',
			strategy_id: accepted.strategy_id,
			dataset_fingerprint: `sha256:${'c'.repeat(64)}`,
			oos_fraction: '0.3'
		});
		expect(finished.study_fingerprint).toBe(STUDY);
		expect(fetchMock).toHaveBeenLastCalledWith(
			`/api/v1/research/studies/${encodeURIComponent(STUDY)}?detail=full`,
			{ headers: { Accept: 'application/json' } }
		);
	});
});
