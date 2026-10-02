import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BuilderModel, Dataset } from './strategies';
import { defaultHtfFilter } from './strategies';

const NOW = new Date('2026-10-01T12:00:00Z');

function model(overrides: Partial<BuilderModel> = {}): BuilderModel {
	return {
		strategy_id: 's',
		revision: 1,
		name: 'Test',
		description: '',
		created_at: '2026-09-01T00:00:00Z',
		product_id: 'BTC-USDC',
		base_currency: 'BTC',
		additional_instruments: [],
		timeframe: '1h',
		warmup_bars: 50,
		indicators: [{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 12 } }],
		htf_filter: null,
		entry: { when: { all: [] } },
		side: 'long',
		sizing: { risk_fraction: '0.01', min_quote_notional: '10', max_quote_notional: '100' },
		portfolio_limits: { max_strategy_exposure_fraction: '0.1', max_concurrent_positions: 1 },
		exits: {
			initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr', multiple: '1.5' },
			take_profit: { kind: 'reward_risk', multiple: '2' },
			trailing_stop: { enabled: false },
			time_exit: { max_bars_held: 96 }
		},
		...overrides
	} as BuilderModel;
}

function dataset(timeframe: string, endsAt = '2026-10-01T11:00:00Z'): Dataset {
	return {
		product_id: 'BTC-USDC',
		timeframe,
		starts_at: '2026-01-01T00:00:00Z',
		ends_at: endsAt,
		content_fingerprint: `sha256:${timeframe.padEnd(64, '0')}`
	};
}

describe('required clocks', () => {
	it('lists execution, HTF, and extra indicator clocks without duplicates', async () => {
		const { requiredClocks } = await import('./data-readiness');
		const clocks = requiredClocks(
			model({
				htf_filter: defaultHtfFilter('1h'),
				indicators: [
					{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 12 } },
					{ id: 'slow4h', kind: 'ema', input: 'close', timeframe: '4h', parameters: { period: 5 } },
					{ id: 'slow2h', kind: 'ema', input: 'close', timeframe: '2h', parameters: { period: 5 } }
				]
			})
		);
		expect(clocks).toEqual([
			{ productId: 'BTC-USDC', timeframe: '1h', role: 'execution' },
			{ productId: 'BTC-USDC', timeframe: '2h', role: 'htf' },
			{ productId: 'BTC-USDC', timeframe: '4h', role: 'indicator' }
		]);
	});

	it('names the exact missing product × timeframe for an HTF filter', async () => {
		const { strategyReadiness, readinessMessage } = await import('./data-readiness');
		const readiness = strategyReadiness(
			model({ htf_filter: defaultHtfFilter('1h') }),
			[dataset('1h')],
			NOW
		);
		expect(readiness.map((item) => item.availability)).toEqual(['ready', 'missing']);
		expect(readinessMessage(readiness[1])).toBe(
			"No verified BTC-USDC × 2h dataset yet. This strategy's higher-timeframe filter clock needs it."
		);
		expect(readinessMessage(readiness[0])).toBeNull();
	});

	it('flags a dataset that ends well before now as stale', async () => {
		const { strategyReadiness, readinessMessage } = await import('./data-readiness');
		const [execution] = strategyReadiness(model(), [dataset('1h', '2026-09-17T04:00:00Z')], NOW);
		expect(execution.availability).toBe('stale');
		expect(readinessMessage(execution)).toContain('ends 2026-09-17 04:00 UTC');
		const [fresh] = strategyReadiness(model(), [dataset('1h', '2026-10-01T10:00:00Z')], NOW);
		expect(fresh.availability).toBe('ready');
	});

	it('ignores datasets for other products', async () => {
		const { strategyReadiness } = await import('./data-readiness');
		const other = { ...dataset('1h'), product_id: 'BTC-USD' };
		expect(strategyReadiness(model(), [other], NOW)[0].availability).toBe('missing');
	});

	it('uses the per-timeframe watch lookback ceilings', async () => {
		const { defaultWatchLookbackHours } = await import('./data-readiness');
		expect(defaultWatchLookbackHours('1m')).toBe(2160);
		expect(defaultWatchLookbackHours('5m')).toBe(8760);
		expect(defaultWatchLookbackHours('15m')).toBe(17520);
		expect(defaultWatchLookbackHours('30m')).toBe(26280);
		expect(defaultWatchLookbackHours('1h')).toBe(43800);
		expect(defaultWatchLookbackHours('2h')).toBe(87600);
		expect(defaultWatchLookbackHours('4h')).toBe(87600);
		expect(defaultWatchLookbackHours('6h')).toBe(87600);
		expect(defaultWatchLookbackHours('1d')).toBe(87600);
		expect(defaultWatchLookbackHours('3d')).toBe(2160);
	});

	it('spells lookbacks in years or days for the confirmation copy', async () => {
		const { describeLookbackHours } = await import('./data-readiness');
		expect(describeLookbackHours(87600)).toBe('10 years');
		expect(describeLookbackHours(8760)).toBe('1 year');
		expect(describeLookbackHours(2160)).toBe('90 days');
		expect(describeLookbackHours(24)).toBe('1 day');
		expect(describeLookbackHours(5)).toBe('5 hours');
	});
});

describe('download progress', () => {
	it('reports backfilling counts, completion, and the provider history floor', async () => {
		const { downloadProgress } = await import('./data-readiness');
		const base = { product_id: 'BTC-USDC', timeframe: '2h', ingest_requested_at: null };
		expect(
			downloadProgress({ ...base, state: { status: 'never_run', watch_complete: false } })
		).toMatchObject({
			done: false,
			watchStatus: 'waiting'
		});
		const partial = downloadProgress({
			...base,
			state: {
				status: 'succeeded',
				watch_complete: false,
				received_candle_count: 120,
				watch_expected_candle_count: 4380
			}
		});
		expect(partial).toMatchObject({ done: false, watchStatus: 'backfilling' });
		expect(partial.text).toBe('Backfilling · 120 of 4380 candles');
		const done = downloadProgress({
			...base,
			state: {
				status: 'succeeded',
				watch_complete: true,
				received_candle_count: 1574,
				watch_expected_candle_count: 4380,
				history_floor_at: '2026-05-09T00:00:00+00:00'
			}
		});
		expect(done.done).toBe(true);
		expect(done.floorNote).toBe(
			'Coinbase has no trades before 2026-05-09 00:00 UTC (the listing); coverage starts there.'
		);
	});
});

describe('requestDatasetDownload', () => {
	beforeEach(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	function stubFetch(targets: unknown[]) {
		const fetchMock = vi.fn().mockImplementation(async (path: string) => {
			if (path === '/api/v1/security/session') {
				return { ok: true, json: async () => ({ csrf_token: 'data-csrf' }) };
			}
			if (path === '/api/v1/data/watchlist') {
				return { ok: true, json: async () => ({ targets }) };
			}
			return {
				ok: true,
				json: async () => ({
					accepted: true,
					product_id: 'BTC-USDC',
					timeframe: '2h',
					ingest_requested_at: '2026-10-01T12:00:00Z',
					state: { status: 'never_run', watch_complete: false }
				})
			};
		});
		vi.stubGlobal('fetch', fetchMock);
		return fetchMock;
	}

	it('watches with the default lookback then queues a no-wait ingest with CSRF', async () => {
		const fetchMock = stubFetch([]);
		const { requestDatasetDownload } = await import('./data-readiness');
		const accepted = await requestDatasetDownload('BTC-USDC', '2h');
		expect(accepted.ingest_requested_at).toBe('2026-10-01T12:00:00Z');
		const calls = fetchMock.mock.calls as [string, RequestInit | undefined][];
		const put = calls.find(([, init]) => init?.method === 'PUT');
		const post = calls.find(([, init]) => init?.method === 'POST');
		expect(put?.[0]).toBe('/api/v1/data/watchlist');
		expect(JSON.parse(String(put?.[1]?.body))).toEqual({
			product_id: 'BTC-USDC',
			timeframe: '2h',
			lookback_hours: 87600,
			enabled: true
		});
		expect((put?.[1]?.headers as Record<string, string>)['X-CSRF-Token']).toBe('data-csrf');
		expect(post?.[0]).toBe('/api/v1/data/ingest');
		expect(JSON.parse(String(post?.[1]?.body))).toEqual({
			product_id: 'BTC-USDC',
			timeframe: '2h'
		});
		expect((post?.[1]?.headers as Record<string, string>)['X-CSRF-Token']).toBe('data-csrf');
	});

	it('keeps an existing enabled watch with a long enough lookback', async () => {
		const fetchMock = stubFetch([
			{ product_id: 'BTC-USDC', timeframe: '2h', lookback_hours: 87600, enabled: true }
		]);
		const { requestDatasetDownload } = await import('./data-readiness');
		await requestDatasetDownload('BTC-USDC', '2h');
		const methods = (fetchMock.mock.calls as [string, RequestInit | undefined][]).map(
			([, init]) => init?.method ?? 'GET'
		);
		expect(methods).not.toContain('PUT');
		expect(methods).toContain('POST');
	});

	it('surfaces the API detail when the watch cannot be stored', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockImplementation(async (path: string, init?: RequestInit) => {
				if (path === '/api/v1/security/session') {
					return { ok: true, json: async () => ({ csrf_token: 't' }) };
				}
				if (init?.method === 'PUT') {
					return {
						ok: false,
						status: 503,
						json: async () => ({ detail: 'Market-data watchlist is unavailable.' })
					};
				}
				return { ok: true, json: async () => ({ targets: [] }) };
			})
		);
		const { requestDatasetDownload } = await import('./data-readiness');
		await expect(requestDatasetDownload('BTC-USDC', '2h')).rejects.toThrow(
			'The data request failed (HTTP 503): Market-data watchlist is unavailable.'
		);
	});
});
