import { describe, expect, it } from 'vitest';
import { downloadProgress } from '$lib/data-readiness';
import { datasetCoverageText, datasetWatchText, type DatasetCoverageRow } from './home-data';

function row(overrides: Partial<DatasetCoverageRow> = {}): DatasetCoverageRow {
	return {
		provider: 'coinbase',
		product_id: 'BONK-USD',
		timeframe: '1m',
		watched: true,
		lookback_hours: 2160,
		worker_status: 'succeeded',
		watch_complete: false,
		complete: false,
		freshness_status: 'fresh',
		covered_starts_at: '2026-10-02T10:57:00Z',
		covered_ends_at: '2026-10-02T10:59:00Z',
		expected_candle_count: 2,
		received_candle_count: 2,
		gap_count: 0,
		missing_intervals: 0,
		sparsity: 'none',
		watch_expected_candle_count: 129600,
		watch_status: 'backfilling',
		history_floor_at: null,
		...overrides
	};
}

describe('dataset coverage (ADR 0095)', () => {
	it('reports a two-minute series for a 90-day watch as 2 of 129600, backfilling', () => {
		const shrunk = row({ watch_covered_candle_count: 2, watch_coverage_ratio: 0 });
		expect(datasetCoverageText(shrunk)).toBe('2 / 129600');
		expect(datasetWatchText(shrunk)).toBe('Backfilling');
	});

	it('counts watch-window bars and names no-trade bars', () => {
		const filled = row({
			watch_complete: true,
			complete: true,
			watch_status: 'complete',
			received_candle_count: 129_700,
			watch_covered_candle_count: 129_600,
			synthetic_no_trade_intervals: 4_210
		});
		expect(datasetCoverageText(filled)).toBe('129600 / 129600 · 4210 no-trade');
		expect(datasetWatchText(filled)).toBe('Complete');
	});

	it('names a listing floor instead of calling a short series complete', () => {
		const young = row({
			watch_status: 'complete',
			watch_covered_candle_count: 8_760,
			watch_expected_candle_count: 43_800,
			history_floor_at: '2025-07-01T00:00:00Z'
		});
		expect(datasetCoverageText(young)).toBe('8760 / 43800');
		expect(datasetWatchText(young)).toBe('Complete from listing');
	});

	it('falls back to received candles from an image without watch coverage', () => {
		expect(datasetCoverageText(row({ received_candle_count: 168 }))).toBe('168 / 129600');
		expect(datasetCoverageText(row({ received_candle_count: null }))).toBe('—');
	});

	it('uses watch coverage for download progress when the API reports it', () => {
		const progress = downloadProgress({
			product_id: 'BONK-USD',
			timeframe: '1m',
			ingest_requested_at: null,
			state: {
				status: 'succeeded',
				watch_complete: false,
				received_candle_count: 2,
				watch_covered_candle_count: 2,
				watch_expected_candle_count: 129_600
			}
		});
		expect(progress.text).toBe('Backfilling · 2 of 129600 candles');
		expect(progress.floorNote).toBeNull();
	});
});
