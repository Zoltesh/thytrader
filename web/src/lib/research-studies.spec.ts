import { describe, expect, it, vi } from 'vitest';
import {
	engineContractLabel,
	listStrategyTemplates,
	parseParameterAxisValues,
	submitResearchStudy
} from './research-studies';

describe('parseParameterAxisValues', () => {
	it('splits comma-separated axis values and drops blanks', () => {
		expect(parseParameterAxisValues('12, 26,')).toEqual(['12', '26']);
	});
});

describe('engineContractLabel', () => {
	it('names V1, V2, and V3 from engine contract versions', () => {
		expect(engineContractLabel('thytrader-bar-backtest-v1')).toBe('V1');
		expect(engineContractLabel('thytrader-bar-backtest-v2')).toBe('V2');
		expect(engineContractLabel('thytrader-bar-backtest-v3')).toBe('V3');
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
		const fetchMock = vi.fn().mockResolvedValue({
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
			strategy_fingerprint: `sha256:${'b'.repeat(64)}`,
			dataset_fingerprint: `sha256:${'c'.repeat(64)}`,
			oos_fraction: '0.3'
		});
		expect(study.kind).toBe('oos_holdout');
		expect(fetchMock).toHaveBeenCalledWith(
			'/api/v1/research/studies',
			expect.objectContaining({ method: 'POST' })
		);
		vi.unstubAllGlobals();
	});
});
