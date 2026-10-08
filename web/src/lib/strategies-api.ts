/** Strategy HTTP client and `StrategyApiError`. Re-exported by `strategies.ts`. */
import {
	isAcceptedResearchJob,
	type ResearchJobAccepted,
	researchJobFailureMessage,
	researchJobFailureStatus,
	waitForResearchJob
} from '$lib/research-jobs';
import { ensureBrowserCsrfSession, mutationHeaders } from '$lib/security';
import type {
	BacktestLaunchInput,
	BulkDeleteResponse,
	Dataset,
	StrategyDeletionResult,
	StrategyDocument,
	StrategyLibraryEntry,
	StrategyRecord,
	StrategySnapshot
} from './strategies-types';
import type { StrategyOrigin } from './strategies-library';

type StrategyLibraryResponse = {
	origin_counts?: Record<StrategyOrigin, number> | null;
	strategies: StrategyLibraryEntry[];
	total?: number;
	has_more?: boolean;
	next_cursor?: string | null;
};

type BacktestSubmission = {
	run_fingerprint: string;
	result_fingerprint: string;
	strategy_id?: string;
	strategy_fingerprint?: string;
};

/**
 * One failed strategy API call. `code` is the structured `detail.code` when the
 * server sent one (for example `strategy_revision_conflict`, `strategy_invalid`,
 * `strategy_has_active_deployments`), so callers can branch without parsing text.
 */
export class StrategyApiError extends Error {
	readonly status: number;
	readonly code: string | null;
	readonly detail: Record<string, unknown>;

	constructor(
		status: number,
		code: string | null,
		message: string,
		detail: Record<string, unknown>
	) {
		super(message);
		this.name = 'StrategyApiError';
		this.status = status;
		this.code = code;
		this.detail = detail;
	}
}

/** Structured error code of a caught value, or null. */
export function strategyErrorCode(caught: unknown): string | null {
	return caught instanceof StrategyApiError ? caught.code : null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const method = init?.method?.toUpperCase() ?? 'GET';
	if (method !== 'GET' && method !== 'HEAD') {
		await ensureBrowserCsrfSession();
	}
	const response = await fetch(url, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...init?.headers,
			...mutationHeaders()
		}
	});
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as {
			detail?: string | ({ message?: string; code?: string } & Record<string, unknown>);
		};
		const detail =
			typeof body.detail === 'string'
				? body.detail
				: (body.detail?.message ?? 'no details returned');
		const structured = typeof body.detail === 'object' && body.detail !== null ? body.detail : {};
		const code = typeof structured.code === 'string' ? structured.code : null;
		throw new StrategyApiError(
			response.status,
			code,
			`The research operation failed (HTTP ${response.status}): ${detail}`,
			structured
		);
	}
	return (await response.json()) as T;
}

function strategyPath(strategyId: string, suffix = ''): string {
	return `/api/v1/strategies/${encodeURIComponent(strategyId)}${suffix}`;
}

/** Create a strategy from a fail-closed research template. */
export async function createStrategy(options?: {
	template?: string;
	product_id?: string;
	timeframe?: string;
}): Promise<StrategyRecord> {
	const params = new URLSearchParams();
	if (options?.template !== undefined && options.template !== 'ema-trend') {
		params.set('template', options.template);
	}
	if (options?.product_id !== undefined && options.product_id !== 'BTC-USD') {
		params.set('product_id', options.product_id);
	}
	if (options?.timeframe !== undefined && options.timeframe !== '1h') {
		params.set('timeframe', options.timeframe);
	}
	const query = params.toString();
	const path = query === '' ? '/api/v1/strategies' : `/api/v1/strategies?${query}`;
	return request<StrategyRecord>(path, { method: 'POST' });
}

export async function fetchStrategy(strategyId: string): Promise<StrategyRecord> {
	return request<StrategyRecord>(strategyPath(strategyId));
}

/**
 * Save the document in place. The revision guard rejects a stale save with 409
 * `strategy_revision_conflict`; it never overwrites. Invalid documents are saved
 * and come back with their validation result.
 */
export async function saveStrategy(
	strategyId: string,
	document: StrategyDocument,
	revision: number
): Promise<StrategyRecord> {
	return request<StrategyRecord>(strategyPath(strategyId), {
		method: 'PUT',
		body: JSON.stringify({ document, revision })
	});
}

export async function deleteStrategy(strategyId: string): Promise<StrategyDeletionResult> {
	return request<StrategyDeletionResult>(strategyPath(strategyId), { method: 'DELETE' });
}

/** Preview (`dryRun`) or confirm deletion of up to 100 strategies; results are per strategy. */
export async function bulkDeleteStrategies(
	strategyIds: string[],
	options: { dryRun: boolean }
): Promise<BulkDeleteResponse> {
	return request<BulkDeleteResponse>('/api/v1/strategies/bulk-delete', {
		method: 'POST',
		body: JSON.stringify({
			strategy_ids: strategyIds,
			confirm: !options.dryRun,
			dry_run: options.dryRun
		})
	});
}

export async function cloneStrategy(strategyId: string): Promise<StrategyRecord> {
	return request<StrategyRecord>(strategyPath(strategyId, '/clone'), { method: 'POST' });
}

/** Import one JSON document as a new strategy (always a fresh identity). */
export async function importStrategy(document: unknown): Promise<StrategyRecord> {
	return request<StrategyRecord>('/api/v1/strategies/import', {
		method: 'POST',
		body: JSON.stringify({ document })
	});
}

/** The exact rules one run or bot used, addressed by snapshot fingerprint. */
export async function fetchStrategySnapshot(fingerprint: string): Promise<StrategySnapshot> {
	return request<StrategySnapshot>(
		`/api/v1/strategies/snapshots/${encodeURIComponent(fingerprint)}`
	);
}

export async function fetchStrategyPage(
	limit: 10 | 25 | 50 | 100,
	cursor?: string,
	tag?: string | null,
	origin: StrategyOrigin = 'all'
): Promise<{
	entries: StrategyLibraryEntry[];
	nextCursor: string | null;
	total: number | null;
	originCounts: Record<StrategyOrigin, number> | null;
}> {
	const params = new URLSearchParams({ limit: String(limit) });
	if (cursor !== undefined) params.set('cursor', cursor);
	if (tag) params.set('tag', tag);
	if (origin !== 'all') params.set('origin', origin);
	const body = await request<StrategyLibraryResponse>(`/api/v1/strategies?${params.toString()}`);
	const hasMore = body.has_more === true;
	if (hasMore && body.strategies.length === 0) {
		throw new Error('Strategy library returned an empty page while claiming more strategies.');
	}
	if (hasMore && !body.next_cursor) {
		throw new Error('Strategy library has more strategies but no next cursor.');
	}
	return {
		entries: body.strategies,
		nextCursor: hasMore ? body.next_cursor! : null,
		total: typeof body.total === 'number' ? body.total : null,
		originCounts: body.origin_counts ?? null
	};
}

export async function listStrategies(
	onPage?: (entries: StrategyLibraryEntry[], hasMore: boolean) => void
): Promise<StrategyLibraryEntry[]> {
	const rows: StrategyLibraryEntry[] = [];
	let cursor: string | undefined;
	for (let page = 0; page < 50; page += 1) {
		const params = new URLSearchParams({ limit: '100' });
		if (cursor !== undefined) params.set('cursor', cursor);
		const body = await request<StrategyLibraryResponse>(`/api/v1/strategies?${params.toString()}`);
		const hasMore = body.has_more === true;
		const nextCursor = body.next_cursor;
		if (hasMore && body.strategies.length === 0) {
			throw new Error('Strategy library returned an empty page while claiming more strategies.');
		}
		if (hasMore && !nextCursor) {
			throw new Error('Strategy library has more strategies but no next cursor.');
		}
		rows.push(...body.strategies);
		onPage?.([...rows], hasMore);
		if (!hasMore) return rows;
		cursor = nextCursor ?? undefined;
	}
	throw new Error('Strategy library truncated: exceeded the 50-page fetch cap.');
}

export async function listDatasets(): Promise<Dataset[]> {
	return (await request<{ datasets: Dataset[] }>('/api/v1/market-data/datasets/latest')).datasets;
}

/**
 * Run one backtest in the research worker. A run longer than the API's synchronous
 * wait comes back as HTTP 202 with the job; poll it to the same submission shape.
 */
export async function submitBacktest(input: BacktestLaunchInput): Promise<BacktestSubmission> {
	const body = await request<BacktestSubmission | ResearchJobAccepted>('/api/v1/backtests', {
		method: 'POST',
		body: JSON.stringify(input)
	});
	if (!isAcceptedResearchJob(body)) return body;
	const job = await waitForResearchJob(body.job_id);
	if (job.status === 'completed' && job.run_fingerprint && job.result_fingerprint) {
		return {
			run_fingerprint: job.run_fingerprint,
			result_fingerprint: job.result_fingerprint,
			strategy_id: body.strategy_id ?? undefined,
			strategy_fingerprint: body.strategy_fingerprint ?? undefined
		};
	}
	const status = researchJobFailureStatus(job);
	throw new StrategyApiError(
		status,
		job.error_code ?? null,
		`The research operation failed (HTTP ${status}): ${researchJobFailureMessage(job)}`,
		{}
	);
}
