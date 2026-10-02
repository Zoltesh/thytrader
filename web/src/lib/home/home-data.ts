/**
 * Read-only Home loaders over existing endpoints (ADR 0084). No new backend
 * contract:
 *
 * - `GET /api/v1/portfolio` and `GET /api/v1/portfolio/history?range=`
 * - `GET /api/v1/operator/data-catalog` (watched datasets; can take ~20 s)
 * - `GET /api/v1/market-data/ingestion?product_id=&timeframe=` (per backfilling dataset)
 * - `GET /api/v1/research/jobs?strategy_id=&limit=` (per recently updated strategy)
 *
 * Every loader throws on failure so its card can show the error with a retry;
 * a 503 from portfolio history is a state ("unavailable on this installation"),
 * not an error.
 */
import type { HistoryEntry, HistoryRange, Portfolio, PortfolioHistory } from '$lib/portfolio';
import type { StrategyLibraryEntry } from '$lib/strategies';
import { clipHistoryToRange, type HomeChartRangeOption } from './history-range';
import { HttpError } from './load';

async function failureMessage(response: Response, fallback: string): Promise<string> {
	try {
		const body = (await response.json()) as { detail?: unknown };
		const detail = body.detail;
		if (typeof detail === 'string' && detail.trim() !== '') return detail;
		if (typeof detail === 'object' && detail !== null && 'message' in detail) {
			const message = (detail as { message?: unknown }).message;
			if (typeof message === 'string' && message.trim() !== '') return message;
		}
	} catch {
		/* keep the fallback; never echo a raw body */
	}
	return `${fallback} (HTTP ${response.status}).`;
}

async function readJson<T>(path: string, fallback: string): Promise<T> {
	const response = await fetch(path, { headers: { Accept: 'application/json' } });
	if (!response.ok) throw new HttpError(response.status, await failureMessage(response, fallback));
	return (await response.json()) as T;
}

/** Current Coinbase (or demo) balances; the redacted API message on failure. */
export async function fetchPortfolio(): Promise<Portfolio> {
	return readJson<Portfolio>('/api/v1/portfolio', 'Portfolio data is unavailable');
}

export type HistoryRead = { kind: 'ready'; history: PortfolioHistory } | { kind: 'unavailable' };

/** One history range; 503 means durable history is not configured here. */
export async function fetchPortfolioHistory(range: HistoryRange): Promise<HistoryRead> {
	try {
		const history = await readJson<PortfolioHistory>(
			`/api/v1/portfolio/history?range=${range}`,
			'Portfolio history could not be loaded'
		);
		return { kind: 'ready', history };
	} catch (caught) {
		if (caught instanceof HttpError && caught.status === 503) return { kind: 'unavailable' };
		throw caught;
	}
}

export type ChartHistory =
	| { kind: 'unavailable'; option: HomeChartRangeOption }
	| {
			kind: 'ready';
			option: HomeChartRangeOption;
			/** Newest-first, clipped to the pill's window. */
			entries: HistoryEntry[];
			/** Entries the API returned before clipping (detects a thinned response). */
			responseCount: number;
			samplingIntervalSeconds: number;
	  };

export async function fetchChartHistory(
	option: HomeChartRangeOption,
	nowMs: number = Date.now()
): Promise<ChartHistory> {
	const read = await fetchPortfolioHistory(option.apiRange);
	if (read.kind === 'unavailable') return { kind: 'unavailable', option };
	return {
		kind: 'ready',
		option,
		entries: clipHistoryToRange(read.history.entries, option, nowMs),
		responseCount: read.history.entries.length,
		samplingIntervalSeconds: read.history.sampling_interval_seconds
	};
}

/** One row of `GET /api/v1/operator/data-catalog` (`DatasetCoverageRow`). */
export type DatasetCoverageRow = {
	provider: string | null;
	product_id: string;
	timeframe: string;
	watched: boolean;
	lookback_hours: number | null;
	worker_status: string | null;
	failure_code?: string | null;
	failure_message?: string | null;
	watch_complete?: boolean | null;
	complete: boolean | null;
	freshness_status: string;
	covered_starts_at: string | null;
	covered_ends_at: string | null;
	expected_candle_count: number | null;
	received_candle_count: number | null;
	gap_count: number | null;
	missing_intervals: number | null;
	sparsity?: string;
	watch_expected_candle_count?: number | null;
	/** `complete`, `backfilling`, or `unknown`; `succeeded` alone never means a finished backfill. */
	watch_status?: 'complete' | 'backfilling' | 'unknown' | null;
	/** Set only when Coinbase has no trades at all before coverage starts (the listing). */
	history_floor_at?: string | null;
	/** Dataset-level completeness; `complete` is watch-relative for watched rows (ADR 0095). */
	island_complete?: boolean | null;
	/** Bars of the watch lookback the verified series covers: the X of "X of Y". */
	watch_covered_candle_count?: number | null;
	watch_coverage_ratio?: number | null;
	/** Flat zero-volume bars published for intervals without trades. */
	synthetic_no_trade_intervals?: number | null;
};

/**
 * "X of Y" watch coverage for one catalog row, naming no-trade bars when present.
 *
 * Prefers the watch-window count (ADR 0095) and falls back to the dataset's received
 * candles for images that predate it.
 */
export function datasetCoverageText(row: DatasetCoverageRow): string {
	const covered = row.watch_covered_candle_count ?? row.received_candle_count;
	const expected = row.watch_expected_candle_count ?? row.expected_candle_count;
	if (covered === null || covered === undefined) return '—';
	const base =
		expected === null || expected === undefined ? `${covered} candles` : `${covered} / ${expected}`;
	const noTrade = row.synthetic_no_trade_intervals ?? 0;
	return noTrade > 0 ? `${base} · ${noTrade} no-trade` : base;
}

/** The watch column: a listing floor is named so a short series is never read as done. */
export function datasetWatchText(row: DatasetCoverageRow): string {
	if (row.watch_status === 'complete')
		return row.history_floor_at ? 'Complete from listing' : 'Complete';
	if (row.watch_status === 'backfilling') return 'Backfilling';
	return 'Unknown';
}

export type DataCatalogReport = {
	generated_at: string;
	overall_status: string;
	partial_result_warnings: string[];
	payload: { datasets: DatasetCoverageRow[] };
};

export async function fetchDataCatalog(): Promise<DataCatalogReport> {
	return readJson<DataCatalogReport>(
		'/api/v1/operator/data-catalog',
		'The data catalog could not be loaded'
	);
}

/** The fields of `GET /api/v1/market-data/ingestion` that Home reads. */
export type IngestionState = {
	product_id: string;
	timeframe: string;
	status: string;
	last_attempt_at: string | null;
	last_success_at: string | null;
	next_attempt_at: string | null;
	watch_complete?: boolean | null;
	failure: { code: string; message: string; consecutive_failures: number } | null;
};

export function datasetKey(productId: string, timeframe: string): string {
	return `${productId}|${timeframe}`;
}

export type IngestionScan = {
	/** Keyed by `datasetKey(product_id, timeframe)`. */
	states: Record<string, IngestionState>;
	/** Datasets checked (bounded by `INGESTION_CHECK_LIMIT`). */
	checked: number;
	/** Datasets whose ingestion state could not be read. */
	unreadable: number;
	/** Candidates beyond the bound that were not checked. */
	skipped: number;
};

/** At most this many backfilling datasets are checked per Home load. */
export const INGESTION_CHECK_LIMIT = 10;

/**
 * Ingestion state for each backfilling watched dataset, read in parallel.
 * Throws only when every read failed (the source is down, not one dataset).
 */
export async function fetchIngestionStates(
	targets: readonly { product_id: string; timeframe: string }[]
): Promise<IngestionScan> {
	const checked = targets.slice(0, INGESTION_CHECK_LIMIT);
	const results = await Promise.allSettled(
		checked.map((target) =>
			readJson<IngestionState>(
				`/api/v1/market-data/ingestion?${new URLSearchParams({
					product_id: target.product_id,
					timeframe: target.timeframe
				}).toString()}`,
				'Ingestion state could not be loaded'
			)
		)
	);
	const firstFailure = results.find(
		(result): result is PromiseRejectedResult => result.status === 'rejected'
	);
	if (
		checked.length > 0 &&
		firstFailure !== undefined &&
		results.every((r) => r.status === 'rejected')
	) {
		throw firstFailure.reason;
	}
	const states: Record<string, IngestionState> = {};
	results.forEach((result, index) => {
		if (result.status !== 'fulfilled') return;
		const target = checked[index];
		states[datasetKey(target.product_id, target.timeframe)] = result.value;
	});
	return {
		states,
		checked: checked.length,
		unreadable: results.filter((result) => result.status === 'rejected').length,
		skipped: Math.max(0, targets.length - checked.length)
	};
}

/** The fields of one `GET /api/v1/research/jobs` record that Home reads. */
export type ResearchJob = {
	job_id: string;
	kind: string;
	status: string;
	created_at: string;
	updated_at: string;
	error_message: string | null;
	failed_phase: string | null;
	failed_detail: string | null;
	strategy_id: string | null;
};

export type StrategyJobs = { strategyId: string; strategyName: string; jobs: ResearchJob[] };

export type ResearchScan = {
	checked: StrategyJobs[];
	/** Library strategies beyond the checked window. */
	unchecked: number;
	/** Checked strategies whose job list could not be read. */
	unreadable: number;
};

/** The research list is per strategy, so Home checks only the most recently updated ones. */
export const RESEARCH_CHECK_LIMIT = 12;
const RESEARCH_JOBS_PER_STRATEGY = 3;

/**
 * Newest research jobs for the most recently updated strategies.
 * Throws only when every read failed.
 */
export async function fetchRecentResearchJobs(
	strategies: readonly StrategyLibraryEntry[]
): Promise<ResearchScan> {
	const recent = [...strategies]
		.sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
		.slice(0, RESEARCH_CHECK_LIMIT);
	const results = await Promise.allSettled(
		recent.map((strategy) =>
			readJson<{ jobs: ResearchJob[] }>(
				`/api/v1/research/jobs?${new URLSearchParams({
					strategy_id: strategy.strategy_id,
					limit: String(RESEARCH_JOBS_PER_STRATEGY)
				}).toString()}`,
				'Research jobs could not be loaded'
			)
		)
	);
	const firstFailure = results.find(
		(result): result is PromiseRejectedResult => result.status === 'rejected'
	);
	if (
		recent.length > 0 &&
		firstFailure !== undefined &&
		results.every((r) => r.status === 'rejected')
	) {
		throw firstFailure.reason;
	}
	const checked: StrategyJobs[] = [];
	results.forEach((result, index) => {
		if (result.status !== 'fulfilled') return;
		const strategy = recent[index];
		checked.push({
			strategyId: strategy.strategy_id,
			strategyName: strategy.name,
			jobs: result.value.jobs ?? []
		});
	});
	return {
		checked,
		unchecked: Math.max(0, strategies.length - recent.length),
		unreadable: results.filter((result) => result.status === 'rejected').length
	};
}
