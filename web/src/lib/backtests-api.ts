/**
 * Backtest results API: list paging and `?result=` helpers, and the list, detail,
 * benchmark, metrics and per-strategy readers. Re-exported by `backtests.ts`.
 */
import { formatUsd } from './portfolio';
import { isRecordedDecimal } from './backtests-format';
import type {
	ApiError,
	BacktestBenchmarkResponse,
	BacktestDetail,
	BacktestList,
	BacktestListQuery,
	BacktestMetricsResponse,
	BacktestSummaryEntry
} from './backtests-types';

/** Default UI page size; the API accepts limits from 1 through 100. */
export const BACKTEST_LIST_DEFAULT_LIMIT = 10;

export const RESULT_FINGERPRINT_PATTERN = /^sha256:[0-9a-f]{64}$/;

export function formatBacktestListBound(
	listing: Pick<BacktestList, 'limit' | 'offset' | 'returned'>
): string {
	/** Disclose the newest-first page bound without implying a complete archive. */
	const { returned, offset } = listing;
	if (offset === 0) {
		return `Showing ${returned} (newest)`;
	}
	return `Showing ${returned} (newest-first, offset ${offset})`;
}

export function backtestListPageIsFull(listing: Pick<BacktestList, 'limit' | 'returned'>): boolean {
	return listing.returned === listing.limit;
}

export function formatListSpreadCue(totalSpreadCost: string | null | undefined): string | null {
	/** Surface recorded modeled spread on a list row; omit when the API did not record one. */
	if (!isRecordedDecimal(totalSpreadCost)) {
		return null;
	}
	return `spread ${formatUsd(totalSpreadCost)} recorded`;
}

export function parseResultFingerprintParam(value: string | null): string | null {
	/** Accept only canonical sha256 result identities from `?result=`. */
	if (value === null || RESULT_FINGERPRINT_PATTERN.exec(value) === null) {
		return null;
	}
	return value;
}

export async function fetchBacktests(
	query: BacktestListQuery = {},
	signal?: AbortSignal
): Promise<BacktestList> {
	const params = new URLSearchParams();
	if (query.limit !== undefined) {
		params.set('limit', String(query.limit));
	}
	if (query.offset !== undefined) {
		params.set('offset', String(query.offset));
	}
	if (query.strategy_fingerprint !== undefined) {
		params.set('strategy_fingerprint', query.strategy_fingerprint);
	}
	if (query.strategy_id !== undefined) {
		params.set('strategy_id', query.strategy_id);
	}
	const search = params.toString();
	const response = await fetch(
		search === '' ? '/api/v1/backtests' : `/api/v1/backtests?${search}`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest results are unavailable.');
	}
	return (await response.json()) as BacktestList;
}

export async function fetchBacktest(
	resultFingerprint: string,
	signal?: AbortSignal
): Promise<BacktestDetail> {
	// The route defaults to `detail=summary` (no `result`, trades, or equity curve);
	// the detail view needs the full document.
	const response = await fetch(
		`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}?detail=full`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest result is unavailable.');
	}
	return (await response.json()) as BacktestDetail;
}

export async function fetchBacktestBenchmark(
	resultFingerprint: string,
	signal?: AbortSignal
): Promise<BacktestBenchmarkResponse> {
	const response = await fetch(
		`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}/benchmark`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest benchmark is unavailable.');
	}
	return (await response.json()) as BacktestBenchmarkResponse;
}

export async function fetchBacktestMetrics(
	resultFingerprint: string,
	signal?: AbortSignal
): Promise<BacktestMetricsResponse> {
	const response = await fetch(
		`/api/v1/backtests/${encodeURIComponent(resultFingerprint)}/metrics`,
		{
			headers: { Accept: 'application/json' },
			signal
		}
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as ApiError;
		throw new Error(body.detail?.message ?? 'Backtest metrics are unavailable.');
	}
	return (await response.json()) as BacktestMetricsResponse;
}

/**
 * Every saved result for one strategy (`?strategy_id=`), newest first,
 * across all of its snapshots. Follows bounded offset pages.
 */
export async function fetchAllBacktestsForStrategy(
	strategyId: string,
	pageSize = 50,
	maxPages = 20
): Promise<BacktestSummaryEntry[]> {
	const rows: BacktestSummaryEntry[] = [];
	let offset = 0;
	for (let page = 0; page < maxPages; page += 1) {
		const listing = await fetchBacktests({ limit: pageSize, offset, strategy_id: strategyId });
		rows.push(...listing.entries);
		if (listing.returned < pageSize || listing.has_more === false) return rows;
		offset += listing.returned;
	}
	return rows;
}
