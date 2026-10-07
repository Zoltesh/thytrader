/** Clock-aware freshness from the all-watch operator report, not the BTC preview. */
export interface WatchedTail {
	product_id: string;
	timeframe: string;
	provider: string | null;
	expected_closed_end: string;
	covered_ends_at: string | null;
	tail_state: 'fresh' | 'settling' | 'stale' | 'missing' | 'invalid';
	lag_seconds: number | null;
	missing_closed_bars: number | null;
	settlement_deadline: string;
	watch_complete: boolean | null;
	island_complete: boolean | null;
	worker_status: string | null;
	failure_code: string | null;
}

export interface DataHealthReport {
	generated_at: string;
	overall_status: 'healthy' | 'degraded' | 'failed';
	partial_result_warnings: string[];
	payload: {
		inventory_complete: boolean;
		watched_count: number;
		attention_count: number;
		datasets: WatchedTail[];
	};
}

/** Failed reads must never be converted into an empty healthy inventory. */
export async function fetchDataHealth(): Promise<DataHealthReport> {
	const response = await fetch('/api/v1/operator/data-health', {
		headers: { Accept: 'application/json' }
	});
	if (!response.ok)
		throw new Error(`Watched data freshness unavailable (HTTP ${response.status}).`);
	return (await response.json()) as DataHealthReport;
}

export function tailDescription(row: WatchedTail): string {
	if (row.tail_state === 'missing') return 'Missing published tail';
	if (row.tail_state === 'invalid') return 'Invalid published timestamp';
	if (row.tail_state === 'fresh') return 'Current closed candle';
	if (row.tail_state === 'settling') return 'Newest close settling';
	return `Stale · ${row.missing_closed_bars ?? '?'} closed bars behind`;
}
