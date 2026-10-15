import { describe, expect, it, vi } from 'vitest';
import {
	fetchResearchStudySummary,
	formatAxisValues,
	formatWindowBounds,
	listStrategyTemplates,
	parametersForTarget,
	parseParameterAxisValues,
	studyHeadline,
	submitResearchStudy,
	type ResearchStudy,
	type StudyWindowResult
} from './research-studies';

describe('parseParameterAxisValues', () => {
	it('splits comma-separated axis values and drops blanks', () => {
		expect(parseParameterAxisValues('12, 26,')).toEqual(['12', '26']);
	});
});

describe('parametersForTarget', () => {
	it('keeps indicator periods and exposes sizing fields', () => {
		expect(parametersForTarget('indicator')).toContain('period');
		expect(parametersForTarget('indicator')).toEqual(
			expect.arrayContaining([
				'multiplier',
				'atr_period',
				'step',
				'max_step',
				'tenkan_period',
				'kijun_period',
				'senkou_b_period',
				'rsi_period',
				'stoch_period',
				'short_period',
				'medium_period',
				'long_period',
				'annualization_periods',
				'offset'
			])
		);
		expect(parametersForTarget('sizing')).toEqual([
			'risk_fraction',
			'min_quote_notional',
			'max_quote_notional'
		]);
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
		const [, init] = fetchMock.mock.calls[1] as [string, { body: string }];
		expect(JSON.parse(init.body)).not.toHaveProperty(['engine', 'contract', 'version'].join('_'));
		vi.unstubAllGlobals();
	});
});

describe('study summary presentation (ADR 0094)', () => {
	it('renders axis values and bounds, with a dash for single-candidate studies', () => {
		expect(formatAxisValues({ 'fast.period': 20, 'slow.period': 200 })).toBe(
			'fast.period=20 · slow.period=200'
		);
		expect(formatAxisValues({})).toBe('—');
		expect(formatAxisValues(undefined)).toBe('—');
		expect(formatWindowBounds('2026-01-01T00:00:00Z', '2026-01-04T00:00:00Z')).toBe(
			'2026-01-01 → 2026-01-04'
		);
	});

	it('reads the persisted study summary', async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					study_fingerprint: 'sha256:' + 'a'.repeat(64),
					kind: 'walk_forward_optimization',
					window_count: 0,
					window_pnl: [],
					candidates: []
				}),
				{ status: 200 }
			)
		);
		vi.stubGlobal('fetch', fetchMock);
		const summary = await fetchResearchStudySummary('sha256:' + 'a'.repeat(64));
		expect(summary.kind).toBe('walk_forward_optimization');
		expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v1/research/studies/sha256%3A');
		vi.unstubAllGlobals();
	});
});

describe('study headline labels', () => {
	const window = (role: StudyWindowResult['role']): StudyWindowResult =>
		({ role }) as unknown as StudyWindowResult;
	const study = (
		roles: StudyWindowResult['role'][],
		aggregate: Partial<ResearchStudy['aggregate']>
	): ResearchStudy =>
		({
			windows: roles.map(window),
			aggregate: {
				oos_window_count: 0,
				oos_trade_count: 0,
				mean_oos_return_fraction: null,
				mean_is_return_fraction: null,
				is_oos_return_gap: null,
				...aggregate
			}
		}) as unknown as ResearchStudy;

	it('names cross-market full windows as full-window, not OOS', () => {
		expect(
			studyHeadline(
				study(['full_window', 'full_window'], {
					full_window_count: 2,
					mean_full_window_return_fraction: '0.02'
				})
			)
		).toEqual({ label: 'full-window', windowCount: 2, meanReturnFraction: '0.02', isOosGap: null });
	});

	it('relabels an older cross-market study that reported full windows under oos_*', () => {
		expect(
			studyHeadline(
				study(['full_window', 'full_window'], {
					oos_window_count: 2,
					mean_oos_return_fraction: '0.02'
				})
			)
		).toEqual({ label: 'full-window', windowCount: 2, meanReturnFraction: '0.02', isOosGap: null });
	});

	it('keeps genuine out-of-sample headlines with their IS−OOS gap', () => {
		expect(
			studyHeadline(
				study(['in_sample', 'out_of_sample'], {
					oos_window_count: 1,
					mean_oos_return_fraction: '0.05',
					is_oos_return_gap: '0.15'
				})
			)
		).toEqual({ label: 'OOS', windowCount: 1, meanReturnFraction: '0.05', isOosGap: '0.15' });
	});
});
