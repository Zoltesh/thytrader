/**
 * Research jobs the research worker runs (ADR 0092).
 *
 * The API never runs research itself. A synchronous `POST /api/v1/backtests` or
 * `POST /api/v1/research/studies` waits server-side for a bounded time; when the
 * research worker has not finished by then the API answers 202 with the queued or
 * running job (`sync_wait_seconds` set) instead of the result. Callers poll
 * `GET /api/v1/research/jobs/{job_id}` until the job is terminal.
 */

export type ResearchJobStatus =
	'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'expired';

/** The fields of one `GET /api/v1/research/jobs/{job_id}` record the UI reads. */
export type ResearchJobRecord = {
	job_id: string;
	kind: 'backtest' | 'study';
	status: ResearchJobStatus;
	progress_current: number;
	progress_total: number;
	error_message?: string | null;
	error_code?: string | null;
	run_fingerprint?: string | null;
	result_fingerprint?: string | null;
	study_fingerprint?: string | null;
};

/** The HTTP 202 body of a queued research submission. */
export type ResearchJobAccepted = {
	job_id: string;
	kind: 'backtest' | 'study';
	status: ResearchJobStatus;
	strategy_id?: string | null;
	strategy_fingerprint?: string | null;
	bound_datasets?: unknown[];
	evaluation_start?: string | null;
	evaluation_end?: string | null;
	sync_wait_seconds?: number | null;
};

const TERMINAL: ReadonlySet<ResearchJobStatus> = new Set([
	'completed',
	'failed',
	'cancelled',
	'expired'
]);

/** Error codes that mean the request itself was rejected (the inline submit's 422). */
const CALLER_INPUT_CODES: ReadonlySet<string> = new Set([
	'backtest_window_rejected',
	'study_window_rejected',
	'study_budget_exceeded'
]);

export function isTerminalResearchJob(status: ResearchJobStatus): boolean {
	return TERMINAL.has(status);
}

/** True for a 202 job body (it names a `job_id`) rather than a finished result. */
export function isAcceptedResearchJob(body: unknown): body is ResearchJobAccepted {
	return (
		typeof body === 'object' &&
		body !== null &&
		typeof (body as { job_id?: unknown }).job_id === 'string'
	);
}

/** The HTTP status the inline submit would have answered for a failed job. */
export function researchJobFailureStatus(job: ResearchJobRecord): number {
	if (job.status === 'cancelled') return 409;
	if (job.status === 'failed' && job.error_code && CALLER_INPUT_CODES.has(job.error_code)) {
		return 422;
	}
	return 503;
}

export function researchJobFailureMessage(job: ResearchJobRecord): string {
	if (job.status === 'cancelled') return `Research job ${job.job_id} was cancelled.`;
	if (job.status === 'expired') return `Research job ${job.job_id} expired before it ran.`;
	return job.error_message ?? 'The research worker could not finish this job.';
}

export type WaitOptions = {
	/** First poll delay; it doubles up to `maxIntervalMs`. */
	intervalMs?: number;
	maxIntervalMs?: number;
	/** Give up (and throw) after this long; the job keeps running server-side. */
	timeoutMs?: number;
	fetchImpl?: typeof fetch;
	sleep?: (ms: number) => Promise<void>;
	onProgress?: (job: ResearchJobRecord) => void;
};

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Poll one research job until it is terminal and return its final record. */
export async function waitForResearchJob(
	jobId: string,
	options: WaitOptions = {}
): Promise<ResearchJobRecord> {
	const fetchImpl = options.fetchImpl ?? fetch;
	const sleep = options.sleep ?? defaultSleep;
	const maxIntervalMs = options.maxIntervalMs ?? 5000;
	const timeoutMs = options.timeoutMs ?? 60 * 60 * 1000;
	let intervalMs = options.intervalMs ?? 1000;
	let waitedMs = 0;
	for (;;) {
		const response = await fetchImpl(`/api/v1/research/jobs/${encodeURIComponent(jobId)}`, {
			headers: { Accept: 'application/json' }
		});
		if (!response.ok) {
			throw new Error(`Could not read research job ${jobId} (HTTP ${response.status}).`);
		}
		const job = (await response.json()) as ResearchJobRecord;
		options.onProgress?.(job);
		if (isTerminalResearchJob(job.status)) return job;
		if (waitedMs >= timeoutMs) {
			throw new Error(
				`Research job ${jobId} is still ${job.status}; it keeps running in the research worker.`
			);
		}
		await sleep(intervalMs);
		waitedMs += intervalMs;
		intervalMs = Math.min(maxIntervalMs, intervalMs * 2);
	}
}
