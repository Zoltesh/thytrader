import { describe, expect, it } from 'vitest';
import { toBuilderModel, type BuilderModel, type StrategyDefinition } from './strategies';
import { semanticDiff } from './strategy-diff';
import { plainEnglishSummary, validateDefinition } from './strategy-insight';

const definition = {
	schema_version: '1.0',
	strategy_id: '01985cf0-7b60-7000-8000-000000000009',
	name: 'Breakout',
	description: null,
	created_at: '2026-09-01T00:00:00Z',
	instrument: { product_id: 'ETH-USDC', base_currency: 'ETH', quote_currency: 'USDC' },
	timeframe: '1h',
	data_requirements: {
		warmup_bars: 21,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [
		{ id: 'close', kind: 'identity', input: 'close', parameters: {} },
		{
			id: 'channel',
			kind: 'donchian',
			input: ['high', 'low'],
			offset: 1,
			parameters: { period: 20 }
		},
		{ id: 'atr', kind: 'atr', input: ['high', 'low', 'close'], parameters: { period: 14 } }
	],
	entry: {
		side: 'long',
		when: {
			all: [
				{
					left: { indicator: 'close' },
					operator: 'crosses_above',
					right: { indicator: 'channel', series: 'upper' }
				}
			]
		},
		cooldown_bars: 3,
		max_open_positions: 1
	},
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.005',
		min_quote_notional: '10',
		max_quote_notional: '100'
	},
	portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 1 },
	exits: {
		initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr', multiple: '2' },
		take_profit: { kind: 'reward_risk', multiple: '2' },
		trailing_stop: { enabled: false },
		time_exit: { max_bars_held: 96 }
	},
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 2,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: [], notes: [] }
} as StrategyDefinition;

function model(): BuilderModel {
	return toBuilderModel(structuredClone(definition), 1);
}

describe('plainEnglishSummary', () => {
	it('quotes sizing in the product quote currency', () => {
		expect(plainEnglishSummary(model())).toContain('between 10 USDC and 100 USDC.');
		const usd = model();
		usd.product_id = 'BTC-USD';
		expect(plainEnglishSummary(usd)).toContain('between 10 USD and 100 USD.');
		const typing = model();
		typing.product_id = 'BTC';
		expect(plainEnglishSummary(typing)).toContain('between 10 quote and 100 quote.');
		expect(plainEnglishSummary(model())).not.toContain('$');
	});
});

describe('validateDefinition with the wider catalog', () => {
	it('accepts a lagged Donchian breakout whose warmup covers period plus offset', () => {
		expect(validateDefinition(model())).toEqual([]);
		const short = model();
		short.warmup_bars = 20;
		expect(validateDefinition(short)).toContain(
			'Warmup must cover the longest indicator period (at least 21 bars).'
		);
	});

	it('reports parameter bounds, constraints, inputs, and offsets from the catalog', () => {
		const broken = model();
		broken.indicators.push(
			{
				id: 'trend',
				kind: 'supertrend',
				input: ['high', 'low', 'close'],
				parameters: { period: 150, multiplier: '0' }
			},
			{
				id: 'cloud',
				kind: 'ichimoku',
				input: ['high', 'low'],
				parameters: { tenkan_period: 30, kijun_period: 26, senkou_b_period: 52 }
			},
			{ id: 'flow', kind: 'obv', input: 'close', parameters: { signal_period: 20 } },
			{ id: 'lag', kind: 'ema', input: 'close', offset: 900, parameters: { period: 20 } },
			{ id: 'level', kind: 'constant', offset: 2, parameters: { value: '40' } }
		);
		const problems = validateDefinition(broken);
		expect(problems).toContain(
			'Indicator "trend" atr period must be an integer between 2 and 100.'
		);
		expect(problems).toContain(
			'Indicator "trend" multiplier must be greater than 0 and at most 10.'
		);
		expect(problems).toContain(
			'Indicator "cloud" Ichimoku needs tenkan period < kijun period < senkou b period.'
		);
		expect(problems).toContain(
			'Indicator "flow" has the wrong input for obv; switch kinds or re-add it.'
		);
		expect(problems).toContain(
			'Indicator "lag" offset must be a whole number of bars between 0 and 500.'
		);
		expect(problems).toContain('Indicator "level" constant must omit offset.');
	});
});

describe('semanticDiff', () => {
	it('labels notional bounds with the quote currency and keeps indicator text', () => {
		const before = model();
		const after = model();
		after.sizing.min_quote_notional = '25';
		after.sizing.max_quote_notional = '250';
		after.indicators[1].parameters.period = 55;
		after.indicators.push({
			id: 'fast',
			kind: 'ema',
			input: 'close',
			timeframe: '',
			parameters: { period: 20 }
		});
		const diff = semanticDiff(before, after);
		const labels = diff.changes.map((change) => change.label);
		expect(labels).toContain('Minimum USDC notional');
		expect(labels).toContain('Maximum USDC notional');
		expect(labels.some((label) => label.includes('USD notional'))).toBe(false);
		const channel = diff.changes.find((change) => change.path === 'indicators.channel');
		expect(channel?.from).toBe('Donchian(20) · 1 bar ago as "channel"');
		expect(channel?.to).toBe('Donchian(55) · 1 bar ago as "channel"');
		const added = diff.changes.find((change) => change.path === 'indicators.fast');
		expect(added?.to).toBe('EMA(20,close) as "fast"');
	});
});
