import { describe, expect, it } from 'vitest';
import { toBuilderModel, type Dataset, type StrategyDefinition } from '$lib/strategies';
import {
	backtestLaunchInput,
	datasetOptionLabel,
	defaultLaunchForm,
	defaultStudyDraft,
	extraLaunchTimeframes,
	indicatorLaunchBindings,
	missingExtraLaunchDatasets,
	periodLabel,
	researchStudyRequest,
	studyCandidateFields,
	studyGeometryFields
} from './launch-plan';

const definition = {
	schema_version: '1.0',
	strategy_id: 'strategy-1',
	name: 'Launch plan',
	description: null,
	created_at: '2026-08-14T12:00:00Z',
	instrument: { product_id: 'BTC-USDC', base_currency: 'BTC', quote_currency: 'USDC' },
	timeframe: '1h',
	data_requirements: { warmup_bars: 10, required_fields: ['open', 'high', 'low', 'close'] },
	indicators: [
		{ id: 'fast', kind: 'ema', input: 'close', parameters: { period: 12 } },
		{ id: 'daily', kind: 'sma', input: 'close', timeframe: '1d', parameters: { period: 20 } }
	],
	entry: {
		side: 'long',
		when: {
			all: [{ left: { indicator: 'fast' }, operator: 'greater_than', right: { literal: '0' } }]
		},
		cooldown_bars: 0,
		max_open_positions: 1
	},
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.01',
		min_quote_notional: '10',
		max_quote_notional: '100'
	},
	portfolio_limits: { max_strategy_exposure_fraction: '0.1', max_concurrent_positions: 1 },
	exits: {
		initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr', multiple: '2' },
		take_profit: { kind: 'reward_risk', multiple: '2' },
		trailing_stop: { enabled: false },
		time_exit: { max_bars_held: 10 }
	},
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 1,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: [], notes: [] }
} as unknown as StrategyDefinition;

const model = toBuilderModel(definition, 1);

function filledForm() {
	return {
		...defaultLaunchForm(),
		dataset_fingerprint: 'ds-1h',
		evaluation_start: '2026-01-01T00:00',
		evaluation_end: '2026-02-01T00:00',
		maker_fee_rate: '0.0025',
		taker_fee_rate: '0.004'
	};
}

describe('study fields', () => {
	it('sends holdout and fold geometry for the matching study kind only', () => {
		const study = defaultStudyDraft();
		expect(studyGeometryFields({ ...study, kind: 'oos_holdout' })).toEqual({
			oos_fraction: '0.3',
			embargo_bars: 0
		});
		expect(studyGeometryFields(study)).toEqual({
			in_sample_bars: 720,
			out_of_sample_bars: 168,
			step_bars: 168,
			fold_mode: 'rolling'
		});
		expect(studyGeometryFields({ ...study, kind: 'parameter_sweep' })).toEqual({});
	});

	it('refuses an axis with fewer than two values or without its indicator id', () => {
		const sweep = { ...defaultStudyDraft(), kind: 'parameter_sweep' as const };
		expect(studyCandidateFields({ ...sweep, axisValues: '12' })).toBe(
			'Enter at least two comma-separated parameter values.'
		);
		expect(studyCandidateFields({ ...sweep, axisIndicatorId: ' ' })).toBe(
			'Enter the indicator id this axis locates.'
		);
		expect(studyCandidateFields(defaultStudyDraft())).toEqual({});
	});

	it('names a non-indicator axis target and drops the indicator id', () => {
		const fields = studyCandidateFields({
			...defaultStudyDraft(),
			kind: 'walk_forward_optimization',
			axisTarget: 'sizing',
			axisParameter: 'risk_fraction',
			axisValues: '0.01, 0.02'
		});
		expect(fields).toEqual({
			parameter_axes: [{ parameter: 'risk_fraction', values: ['0.01', '0.02'], target: 'sizing' }],
			selection_metric: 'total_return_fraction'
		});
	});
});

describe('datasets and window', () => {
	it('labels a dataset by clock and UTC coverage', () => {
		const dataset = {
			product_id: 'BTC-USDC',
			timeframe: '1h',
			starts_at: '2025-08-01T00:00:00Z',
			ends_at: '2026-09-28T00:00:00Z',
			content_fingerprint: 'ds-1h'
		} as Dataset;
		expect(datasetOptionLabel(dataset)).toBe('1h · 2025-08-01 00:00 – 2026-09-28 00:00 UTC');
	});

	it('binds only chosen datasets for indicator clocks beyond the decision clock', () => {
		expect(extraLaunchTimeframes(model)).toEqual(['1d']);
		expect(missingExtraLaunchDatasets(model, {})).toBe(true);
		expect(indicatorLaunchBindings(model, {})).toEqual([]);
		expect(indicatorLaunchBindings(model, { '1d': 'ds-1d' })).toEqual([
			{ timeframe: '1d', dataset_fingerprint: 'ds-1d' }
		]);
		expect(missingExtraLaunchDatasets(model, { '1d': 'ds-1d' })).toBe(false);
	});

	it('reads the period as unset, full coverage, or the chosen dates', () => {
		const bounds = { min: '2026-01-01T00:00', max: '2026-02-01T00:00' };
		expect(periodLabel(defaultLaunchForm(), bounds)).toBe('Not set');
		expect(periodLabel(filledForm(), bounds)).toBe('Full coverage');
		expect(periodLabel(filledForm(), null)).toBe('2026-01-01 → 2026-02-01');
	});
});

describe('request bodies', () => {
	it('omits blank optional datasets and a zero spread stress', () => {
		const input = backtestLaunchInput('strategy-1', { ...filledForm(), spread_bps: '0' }, model);
		expect(input).not.toHaveProperty('htf_dataset_fingerprint');
		expect(input).not.toHaveProperty('indicator_dataset_fingerprints');
		expect(input).not.toHaveProperty('spread_bps');
		expect(input.evaluation_start).toBe('2026-01-01T00:00:00.000Z');
	});

	it('builds a study over the same launch fields, in the same key order', () => {
		const form = {
			...filledForm(),
			indicator_dataset_fingerprints: { '1d': 'ds-1d' },
			spread_bps: '4'
		};
		const body = researchStudyRequest('strategy-1', form, model, defaultStudyDraft(), {});
		expect(Object.keys(body)).toEqual([
			'schema_version',
			'kind',
			'strategy_id',
			'dataset_fingerprint',
			'indicator_dataset_fingerprints',
			'evaluation_start',
			'evaluation_end',
			'initial_quote_balance',
			'maker_fee_rate',
			'taker_fee_rate',
			'fixed_slippage_bps',
			'spread_bps',
			'in_sample_bars',
			'out_of_sample_bars',
			'step_bars',
			'fold_mode'
		]);
		expect(body.kind).toBe('walk_forward');
		expect(body.spread_bps).toBe('4');
	});
});
