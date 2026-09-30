import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	defaultLaunchEngine,
	engineContractLabel,
	fetchEngineSupport,
	listStrategyTemplates,
	parametersForTarget,
	parseParameterAxisValues,
	submitResearchStudy
} from './research-studies';

describe('parseParameterAxisValues', () => {
	it('splits comma-separated axis values and drops blanks', () => {
		expect(parseParameterAxisValues('12, 26,')).toEqual(['12', '26']);
	});
});

describe('parametersForTarget', () => {
	it('keeps indicator periods and exposes sizing fields', () => {
		expect(parametersForTarget('indicator')).toContain('period');
		expect(parametersForTarget('sizing')).toEqual([
			'risk_fraction',
			'min_quote_notional',
			'max_quote_notional'
		]);
	});
});

describe('engineContractLabel', () => {
	it('names V1, V2, and V3 from engine contract versions', () => {
		expect(engineContractLabel('thytrader-bar-backtest-v1')).toBe('V1');
		expect(engineContractLabel('thytrader-bar-backtest-v2')).toBe('V2');
		expect(engineContractLabel('thytrader-bar-backtest-v3')).toBe('V3');
	});

	it('names V4 instead of mislabelling it V1', () => {
		expect(engineContractLabel('thytrader-bar-backtest-v4')).toBe('V4');
	});
});

describe('defaultLaunchEngine', () => {
	it('picks the newest advertised engine this launcher offers', () => {
		expect(
			defaultLaunchEngine([
				'thytrader-bar-backtest-v1',
				'thytrader-bar-backtest-v2',
				'thytrader-bar-backtest-v3',
				'thytrader-bar-backtest-v4'
			])
		).toBe('thytrader-bar-backtest-v3');
		expect(defaultLaunchEngine(['thytrader-bar-backtest-v1', 'thytrader-bar-backtest-v2'])).toBe(
			'thytrader-bar-backtest-v2'
		);
	});

	it('keeps an explicit choice when support is unknown or nothing matches', () => {
		expect(defaultLaunchEngine(null)).toBe('');
		expect(defaultLaunchEngine(['thytrader-bar-backtest-v9'])).toBe('');
	});
});

describe('fetchEngineSupport', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('reads the engine list and fails closed on a malformed body', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: async () => ({
					contract_version: 'thytrader-engine-support-v2',
					engines: ['thytrader-bar-backtest-v1']
				})
			})
		);
		await expect(fetchEngineSupport()).resolves.toEqual({
			contract_version: 'thytrader-engine-support-v2',
			engines: ['thytrader-bar-backtest-v1']
		});
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
		await expect(fetchEngineSupport()).rejects.toThrow(/malformed/);
	});
});

describe('listStrategyTemplates', () => {
	it('reads fail-closed template identities', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({
				templates: [{ id: 'ema-trend', name: 'EMA trend', description: 'reference' }]
			})
		});
		vi.stubGlobal('fetch', fetchMock);
		await expect(listStrategyTemplates()).resolves.toEqual([
			{ id: 'ema-trend', name: 'EMA trend', description: 'reference' }
		]);
		vi.unstubAllGlobals();
	});
});

describe('submitResearchStudy', () => {
	it('posts the study contract and returns the derived document', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ csrf_token: 'test-csrf' }) })
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					study_fingerprint: `sha256:${'a'.repeat(64)}`,
					kind: 'oos_holdout',
					engine_contract_version: 'thytrader-bar-backtest-v1',
					windows: [],
					aggregate: {
						oos_window_count: 1,
						oos_trade_count: 0,
						mean_oos_return_fraction: '0.01',
						mean_is_return_fraction: '0.02',
						is_oos_return_gap: '0.01'
					},
					warnings: []
				})
			});
		vi.stubGlobal('fetch', fetchMock);
		const study = await submitResearchStudy({
			schema_version: 'thytrader-research-study-v1',
			kind: 'oos_holdout',
			evaluation_start: '2026-01-01T00:00:00Z',
			evaluation_end: '2026-01-11T00:00:00Z',
			initial_quote_balance: '10000',
			maker_fee_rate: '0.001',
			taker_fee_rate: '0.002',
			fixed_slippage_bps: '10',
			engine_contract_version: 'thytrader-bar-backtest-v1',
			strategy_id: '01985cf0-7b60-7000-8000-000000000003',
			dataset_fingerprint: `sha256:${'c'.repeat(64)}`,
			oos_fraction: '0.3'
		});
		expect(study.kind).toBe('oos_holdout');
		expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/security/session', {
			headers: { Accept: 'application/json' }
		});
		expect(fetchMock).toHaveBeenNthCalledWith(
			2,
			'/api/v1/research/studies',
			expect.objectContaining({
				method: 'POST',
				headers: { 'content-type': 'application/json', 'X-CSRF-Token': 'test-csrf' }
			})
		);
		vi.unstubAllGlobals();
	});
});
