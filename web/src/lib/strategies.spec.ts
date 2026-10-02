import { describe, expect, it } from 'vitest';
import { INDICATOR_KINDS } from './indicator-catalog';
import {
	applyIndicatorKindDefaults,
	builderModelFromRecord,
	bulkOutcomeText,
	deletionCountsText,
	latestDatasets,
	datasetEvaluationWindow,
	INDICATOR_KIND_OPTIONS,
	INDICATOR_OUTPUT_SERIES,
	isConfigurableRollingKind,
	operandChoices,
	quoteLabelFor,
	STRATEGY_TEMPLATE_OPTIONS,
	type IndicatorDraft,
	parseIndicatorOperandKey,
	researchWindowHint,
	StrategyApiError,
	strategyErrorCode,
	serializeIndicator,
	toBuilderModel,
	fromBuilderModel,
	quoteCurrencyFor,
	unboundIndicatorTimeframes,
	validHtfTimeframes
} from './strategies';

describe('latestDatasets', () => {
	it('keeps one latest revision per product and timeframe', () => {
		const datasets = [
			{
				product_id: 'BTC-USD',
				timeframe: '1h',
				starts_at: '2026-01-01T00:00:00Z',
				ends_at: '2026-02-01T00:00:00Z',
				content_fingerprint: 'sha256:old1h'
			},
			{
				product_id: 'BTC-USD',
				timeframe: '1h',
				starts_at: '2026-01-01T00:00:00Z',
				ends_at: '2026-03-01T00:00:00Z',
				content_fingerprint: 'sha256:new1h'
			},
			{
				product_id: 'BTC-USD',
				timeframe: '1d',
				starts_at: '2026-01-01T00:00:00Z',
				ends_at: '2026-03-01T00:00:00Z',
				content_fingerprint: 'sha256:1d'
			}
		];
		const latest = latestDatasets(datasets);
		expect(latest.map((dataset) => dataset.content_fingerprint).sort()).toEqual([
			'sha256:1d',
			'sha256:new1h'
		]);
	});
});

describe('validHtfTimeframes', () => {
	it('allows coarser integer multiples only', () => {
		expect(validHtfTimeframes('1m')).toEqual(['5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d']);
		expect(validHtfTimeframes('5m')).toEqual(['15m', '30m', '1h', '2h', '4h', '6h', '1d']);
		expect(validHtfTimeframes('1h')).toEqual(['2h', '4h', '6h', '1d']);
		expect(validHtfTimeframes('15m')).toEqual(['30m', '1h', '2h', '4h', '6h', '1d']);
		expect(validHtfTimeframes('4h')).toEqual(['1d']);
		expect(validHtfTimeframes('3h')).toEqual([]);
	});
});

describe('unboundIndicatorTimeframes', () => {
	it('omits the decision clock and HTF-filter clock', () => {
		expect(
			unboundIndicatorTimeframes(
				[
					{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
					{
						id: 'hour_sma',
						kind: 'sma',
						input: 'close',
						timeframe: '1h',
						parameters: { period: 20 }
					},
					{
						id: 'day_sma',
						kind: 'sma',
						input: 'close',
						timeframe: '1d',
						parameters: { period: 20 }
					}
				],
				'5m',
				'1h'
			)
		).toEqual(['1d']);
	});
});

describe('datasetEvaluationWindow', () => {
	const dataset = {
		product_id: 'BTC-USD',
		timeframe: '1m',
		starts_at: '2026-07-10T00:00:00Z',
		ends_at: '2026-07-10T01:00:00Z',
		content_fingerprint: 'sha256:1m'
	};

	it('spaces the usable window by the strategy bar duration', () => {
		expect(datasetEvaluationWindow(dataset, 2, '1m')).toEqual({
			min: '2026-07-10T00:02',
			max: '2026-07-10T00:59'
		});
		expect(datasetEvaluationWindow(dataset, 1, '15m')).toEqual({
			min: '2026-07-10T00:15',
			max: '2026-07-10T00:45'
		});
	});
});

describe('researchWindowHint', () => {
	const bounds = { min: '2026-06-03T02:00', max: '2026-07-31T23:00' };

	it('names 1h bars instead of UTC hours', () => {
		expect(researchWindowHint(bounds, 50, '1h')).toBe(
			'Usable window for this dataset: 2026-06-03 02:00 → 2026-07-31 23:00 (UTC, 1h bars). It must fit inside the dataset with 50 warmup bars before it and one candle after it.'
		);
	});

	it('names 5m bars for five-minute strategies', () => {
		expect(researchWindowHint(bounds, 50, '5m')).toContain('(UTC, 5m bars)');
		expect(researchWindowHint(bounds, 50, '5m')).not.toContain('UTC hours');
	});

	it('falls back to 1h when timeframe is blank', () => {
		expect(researchWindowHint(bounds, 0, '  ')).toContain('(UTC, 1h bars)');
	});
});

const zeroCounts = {
	snapshots: 0,
	backtests: 0,
	research_runs: 0,
	studies: 0,
	research_jobs: 0,
	dataset_bindings: 0,
	paper_deployments: 0,
	live_deployments_kept: 0,
	allocations_removed: 0
};

describe('deletionCountsText', () => {
	it('lists what goes and states that live history is kept', () => {
		const lines = deletionCountsText({
			...zeroCounts,
			snapshots: 2,
			backtests: 3,
			studies: 1,
			paper_deployments: 1,
			live_deployments_kept: 1
		});
		expect(lines).toContain('3 backtests');
		expect(lines).toContain('1 study');
		expect(lines).toContain('1 paper bot (with its ledger)');
		expect(lines).toContain('2 rules snapshots');
		expect(lines.at(-1)).toBe('1 stopped live bot kept with full history');
	});

	it('says so when nothing but the strategy goes', () => {
		expect(deletionCountsText(zeroCounts)).toEqual(['No backtests, studies, or bots']);
	});

	it('names portfolio sleeves the deletion removes', () => {
		expect(deletionCountsText({ ...zeroCounts, portfolio_sleeves: 2 })).toEqual([
			'2 portfolio sleeves (journaled in their portfolios)'
		]);
		expect(deletionCountsText({ ...zeroCounts, portfolio_sleeves: 1 })).toEqual([
			'1 portfolio sleeve (journaled in its portfolio)'
		]);
	});
});

describe('bulkOutcomeText', () => {
	const base = {
		strategy_id: 's',
		name: 'n',
		code: null,
		message: null,
		deployment_ids: [] as string[],
		counts: null
	};
	it('explains a block by running or paused bots', () => {
		expect(
			bulkOutcomeText({
				...base,
				outcome: 'blocked',
				code: 'strategy_has_active_deployments',
				deployment_ids: ['d1', 'd2']
			})
		).toBe('Blocked: 2 running or paused bots. Stop them first.');
	});
	it('reports partial failures with the server message', () => {
		expect(bulkOutcomeText({ ...base, outcome: 'failed', message: 'storage down' })).toBe(
			'Failed: storage down'
		);
		expect(bulkOutcomeText({ ...base, outcome: 'deleted' })).toBe('Deleted');
		expect(bulkOutcomeText({ ...base, outcome: 'not_found' })).toContain('Not found');
	});
});

describe('builderModelFromRecord', () => {
	it('returns null for a work-in-progress document the form cannot show', () => {
		expect(
			builderModelFromRecord({
				strategy_id: 's',
				name: 'WIP',
				revision: 3,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: '2026-01-01T00:00:00Z',
				document: { strategy_id: 's', name: 'WIP' },
				strategy: null,
				validation: { valid: false, issues: [{ loc: 'instrument', message: 'Field required' }] },
				current_fingerprint: null,
				summary: null,
				product_id: null,
				timeframe: null
			})
		).toBeNull();
	});
});

describe('strategyErrorCode', () => {
	it('reads the structured detail code only from StrategyApiError', () => {
		const error = new StrategyApiError(409, 'strategy_revision_conflict', 'stale', {
			current_revision: 4
		});
		expect(strategyErrorCode(error)).toBe('strategy_revision_conflict');
		expect(error.detail.current_revision).toBe(4);
		expect(strategyErrorCode(new Error('x'))).toBeNull();
	});
});

describe('indicator kind picker', () => {
	it('lists every catalog kind in catalog order with the historical labels', () => {
		expect(INDICATOR_KIND_OPTIONS.map((option) => option.kind)).toEqual([...INDICATOR_KINDS]);
		expect(INDICATOR_KIND_OPTIONS.slice(0, 21).map((option) => option.label)).toEqual([
			'EMA',
			'SMA',
			'RSI',
			'ATR',
			'Volume SMA',
			'Highest',
			'Lowest',
			'Stdev',
			'Sample stdev',
			'ROC',
			'Williams %R',
			'CCI',
			'WMA',
			'Momentum',
			'MFI',
			'MACD',
			'Bollinger',
			'Stochastic',
			'ADX',
			'OHLCV',
			'Constant'
		]);
		expect(INDICATOR_OUTPUT_SERIES.supertrend).toEqual(['value', 'direction']);
		expect(INDICATOR_OUTPUT_SERIES.ichimoku).toEqual(['tenkan', 'kijun', 'senkou_a', 'senkou_b']);
		expect(INDICATOR_OUTPUT_SERIES.ema).toBeUndefined();
		expect(isConfigurableRollingKind('zscore')).toBe(true);
		expect(isConfigurableRollingKind('identity')).toBe(false);
		expect(isConfigurableRollingKind('rsi')).toBe(false);
	});

	it('serializes identity without period and constant without input', () => {
		expect(
			serializeIndicator({
				id: 'px',
				kind: 'identity',
				input: 'close',
				parameters: {}
			})
		).toEqual({ id: 'px', kind: 'identity', input: 'close', parameters: {} });
		expect(
			serializeIndicator({
				id: 'rsi_level',
				kind: 'constant',
				parameters: { value: '40' }
			})
		).toEqual({ id: 'rsi_level', kind: 'constant', parameters: { value: '40' } });
	});

	it('expands MACD and Bollinger into series operand choices', () => {
		expect(
			operandChoices([
				{
					id: 'trend_macd',
					kind: 'macd',
					input: 'close',
					parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }
				},
				{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } }
			]).map((choice) => choice.key)
		).toEqual([
			'indicator:trend_macd.macd',
			'indicator:trend_macd.signal',
			'indicator:trend_macd.histogram',
			'indicator:ema_fast',
			'literal'
		]);
		expect(parseIndicatorOperandKey('indicator:trend_macd.histogram')).toEqual({
			indicator: 'trend_macd',
			series: 'histogram'
		});
		expect(parseIndicatorOperandKey('indicator:ema_fast')).toEqual({ indicator: 'ema_fast' });
	});

	it('serializes MACD periods and Bollinger multiplier', () => {
		expect(
			serializeIndicator({
				id: 'trend_macd',
				kind: 'macd',
				input: 'close',
				parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }
			})
		).toEqual({
			id: 'trend_macd',
			kind: 'macd',
			input: 'close',
			parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }
		});
		expect(
			serializeIndicator({
				id: 'bands',
				kind: 'bollinger',
				input: 'close',
				parameters: { period: 20, stdev_multiplier: '2' }
			})
		).toEqual({
			id: 'bands',
			kind: 'bollinger',
			input: 'close',
			parameters: { period: 20, stdev_multiplier: '2' }
		});
		expect(
			serializeIndicator({
				id: 'stoch',
				kind: 'stochastic',
				input: ['high', 'low', 'close'],
				parameters: { k_period: 14, d_period: 3 }
			})
		).toEqual({
			id: 'stoch',
			kind: 'stochastic',
			input: ['high', 'low', 'close'],
			parameters: { k_period: 14, d_period: 3 }
		});
		expect(
			serializeIndicator({
				id: 'sma_high',
				kind: 'sma',
				input: 'high',
				parameters: { period: 20 }
			})
		).toEqual({
			id: 'sma_high',
			kind: 'sma',
			input: 'high',
			parameters: { period: 20 }
		});
		expect(
			serializeIndicator(
				{
					id: 'hour_sma',
					kind: 'sma',
					input: 'close',
					timeframe: '1h',
					parameters: { period: 20 }
				},
				'5m'
			)
		).toEqual({
			id: 'hour_sma',
			kind: 'sma',
			input: 'close',
			parameters: { period: 20 },
			timeframe: '1h'
		});
		expect(
			serializeIndicator(
				{
					id: 'ema_fast',
					kind: 'ema',
					input: 'close',
					timeframe: '5m',
					parameters: { period: 20 }
				},
				'5m'
			)
		).toEqual({
			id: 'ema_fast',
			kind: 'ema',
			input: 'close',
			parameters: { period: 20 }
		});
	});
});

describe('builder multi-instrument pass-through', () => {
	it('round-trips additional instruments, concurrent books, and pyramiding', () => {
		const draft = {
			schema_version: '1.0',
			strategy_id: '01985cf0-7b60-7000-8000-000000000007',
			name: 'Pass-through',
			description: null,
			created_at: '2026-08-14T12:00:00Z',
			instrument: { product_id: 'BTC-USD', base_currency: 'BTC', quote_currency: 'USD' },
			additional_instruments: [
				{ product_id: 'ETH-USD', base_currency: 'ETH', quote_currency: 'USD' }
			],
			timeframe: '1h',
			data_requirements: {
				warmup_bars: 50,
				required_fields: ['open', 'high', 'low', 'close', 'volume']
			},
			indicators: [{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } }],
			entry: {
				side: 'long' as const,
				when: { all: [] },
				cooldown_bars: 3,
				max_open_positions: 3,
				pyramiding: { enabled: true as const, require_unrealized_profit: true as const }
			},
			sizing: {
				kind: 'risk_fraction',
				risk_fraction: '0.005',
				min_quote_notional: '10',
				max_quote_notional: '100'
			},
			portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 2 },
			exits: {
				initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr', multiple: '2' },
				take_profit: { kind: 'reward_risk', multiple: '2' },
				trailing_stop: { enabled: false as const },
				time_exit: { max_bars_held: 96 }
			},
			execution: {
				entry_preference: 'maker_only',
				max_entry_wait_bars: 2,
				on_unfilled_entry: 'cancel'
			},
			metadata: { tags: [], notes: [] }
		};
		const model = toBuilderModel(draft, 4);
		expect(model.additional_instruments).toEqual([
			{ product_id: 'ETH-USD', base_currency: 'ETH', quote_currency: 'USD' }
		]);
		expect(model.max_open_positions).toBe(3);
		expect(model.pyramiding).toEqual({ enabled: true, require_unrealized_profit: true });
		expect(model.portfolio_limits.max_concurrent_positions).toBe(2);
		const saved = fromBuilderModel(model);
		expect(saved.additional_instruments).toEqual(draft.additional_instruments);
		expect((saved.entry as { max_open_positions: number }).max_open_positions).toBe(3);
		expect((saved.entry as { pyramiding: object }).pyramiding).toEqual({
			enabled: true,
			require_unrealized_profit: true
		});
		expect(saved.portfolio_limits.max_concurrent_positions).toBe(2);
	});

	it('omits empty additional instruments and omitted pyramiding', () => {
		const draft = {
			schema_version: '1.0',
			strategy_id: '01985cf0-7b60-7000-8000-000000000008',
			name: 'Single',
			description: null,
			created_at: '2026-08-14T12:00:00Z',
			instrument: { product_id: 'BTC-USD', base_currency: 'BTC', quote_currency: 'USD' },
			timeframe: '1h',
			data_requirements: {
				warmup_bars: 50,
				required_fields: ['open', 'high', 'low', 'close', 'volume']
			},
			indicators: [{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } }],
			entry: {
				side: 'long' as const,
				when: { all: [] },
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
				trailing_stop: { enabled: false as const },
				time_exit: { max_bars_held: 96 }
			},
			execution: {
				entry_preference: 'maker_only',
				max_entry_wait_bars: 2,
				on_unfilled_entry: 'cancel'
			},
			metadata: { tags: [], notes: [] }
		};
		const saved = fromBuilderModel(toBuilderModel(draft, 0));
		expect(saved.additional_instruments).toBeUndefined();
		expect((saved.entry as { pyramiding?: object }).pyramiding).toBeUndefined();
		expect((saved.entry as { max_open_positions: number }).max_open_positions).toBe(1);

		// USDC markets must keep their quote so product_id == base-quote on the backend.
		const usdc = {
			...draft,
			instrument: { product_id: 'BTC-USDC', base_currency: 'BTC', quote_currency: 'USDC' },
			additional_instruments: [
				{ product_id: 'ETH-USDC', base_currency: 'ETH', quote_currency: 'USDC' }
			],
			portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 2 }
		};
		const savedUsdc = fromBuilderModel(toBuilderModel(usdc, 0));
		expect(savedUsdc.instrument).toEqual(usdc.instrument);
		expect(savedUsdc.additional_instruments).toEqual(usdc.additional_instruments);
	});

	it('derives the quote currency from a BASE-QUOTE product id', () => {
		expect(quoteCurrencyFor('BTC-USDC')).toBe('USDC');
		expect(quoteCurrencyFor('UNI-USD')).toBe('USD');
		expect(quoteCurrencyFor('malformed', 'USDC')).toBe('USDC');
		expect(quoteLabelFor('eth-usdc')).toBe('USDC');
		expect(quoteLabelFor('BTC-')).toBe('quote');
		expect(quoteLabelFor('BTC')).toBe('quote');
	});
});

describe('catalog-driven indicator drafts', () => {
	it('serializes new kinds with locked field tuples and declared parameters only', () => {
		expect(
			serializeIndicator({
				id: 'trend',
				kind: 'supertrend',
				input: ['high', 'low', 'close'],
				parameters: { period: 10, multiplier: '3' }
			})
		).toEqual({
			id: 'trend',
			kind: 'supertrend',
			input: ['high', 'low', 'close'],
			parameters: { period: 10, multiplier: '3' }
		});
		expect(
			serializeIndicator({
				id: 'channel',
				kind: 'donchian',
				input: ['high', 'low'],
				parameters: { period: 20 }
			})
		).toEqual({
			id: 'channel',
			kind: 'donchian',
			input: ['high', 'low'],
			parameters: { period: 20 }
		});
		expect(
			serializeIndicator({
				id: 'flow',
				kind: 'obv',
				input: ['close', 'volume'],
				parameters: { signal_period: 20 }
			})
		).toEqual({
			id: 'flow',
			kind: 'obv',
			input: ['close', 'volume'],
			parameters: { signal_period: 20 }
		});
	});

	it('omits a blank optional parameter and keeps a set one', () => {
		const base: IndicatorDraft = {
			id: 'hv',
			kind: 'historical_volatility',
			input: 'close',
			parameters: { period: 20 }
		};
		expect(serializeIndicator(base).parameters).toEqual({ period: 20 });
		expect(
			serializeIndicator({ ...base, parameters: { period: 20, annualization_periods: 365 } })
				.parameters
		).toEqual({ period: 20, annualization_periods: 365 });
	});

	it('emits offset only as a positive bar lag on kinds that take one', () => {
		const prior: IndicatorDraft = {
			id: 'prior_high',
			kind: 'highest',
			input: 'high',
			offset: 1,
			parameters: { period: 20 }
		};
		expect(serializeIndicator(prior)).toEqual({
			id: 'prior_high',
			kind: 'highest',
			input: 'high',
			parameters: { period: 20 },
			offset: 1
		});
		expect(serializeIndicator({ ...prior, offset: 0 })).not.toHaveProperty('offset');
		expect(serializeIndicator({ ...prior, offset: 2.5 })).not.toHaveProperty('offset');
		expect(
			serializeIndicator({ id: 'level', kind: 'constant', offset: 3, parameters: { value: '30' } })
		).toEqual({ id: 'level', kind: 'constant', parameters: { value: '30' } });
	});

	it('switches kinds with catalog defaults, carrying only valid same-named parameters', () => {
		const indicator: IndicatorDraft = {
			id: 'x',
			kind: 'sma',
			input: 'high',
			timeframe: '',
			offset: 2,
			parameters: { period: 50 }
		};
		indicator.kind = 'ema';
		applyIndicatorKindDefaults(indicator);
		expect(indicator).toMatchObject({ input: 'high', offset: 2, parameters: { period: 50 } });

		indicator.kind = 'supertrend';
		applyIndicatorKindDefaults(indicator);
		expect(indicator.input).toEqual(['high', 'low', 'close']);
		expect(indicator.parameters).toEqual({ period: 50, multiplier: '3' });

		indicator.kind = 'rsi';
		applyIndicatorKindDefaults(indicator);
		expect(indicator.input).toBe('close');
		expect(indicator.parameters).toEqual({ period: 50 });

		indicator.kind = 'cmo';
		indicator.parameters = { period: 400 };
		applyIndicatorKindDefaults(indicator);
		expect(indicator.parameters).toEqual({ period: 9 });

		indicator.kind = 'kama';
		indicator.parameters = { period: 10, fast_period: 40, slow_period: 30 };
		applyIndicatorKindDefaults(indicator);
		expect(indicator.parameters).toEqual({ period: 10, fast_period: 2, slow_period: 30 });

		indicator.kind = 'constant';
		applyIndicatorKindDefaults(indicator);
		expect(indicator).not.toHaveProperty('input');
		expect(indicator).not.toHaveProperty('timeframe');
		expect(indicator).not.toHaveProperty('offset');
		expect(indicator.parameters).toEqual({ value: '50' });

		indicator.kind = 'identity';
		applyIndicatorKindDefaults(indicator);
		expect(indicator.input).toBe('close');
		expect(indicator.parameters).toEqual({});
	});

	it('labels operands readably and groups multi-series indicators', () => {
		const choices = operandChoices([
			{
				id: 'trend',
				kind: 'supertrend',
				input: ['high', 'low', 'close'],
				parameters: { period: 10, multiplier: '3' }
			},
			{ id: 'fast', kind: 'sma', input: 'high', parameters: { period: 50 } },
			{ id: 'close', kind: 'identity', input: 'close', parameters: {} },
			{
				id: 'prior',
				kind: 'donchian',
				input: ['high', 'low'],
				offset: 1,
				parameters: { period: 20 }
			},
			{ id: 'zero', kind: 'constant', parameters: { value: '0' } }
		]);
		expect(choices).toEqual([
			{
				key: 'indicator:trend.value',
				label: 'Supertrend(10, 3) · value',
				group: 'Supertrend(10, 3) — trend'
			},
			{
				key: 'indicator:trend.direction',
				label: 'Supertrend(10, 3) · direction',
				group: 'Supertrend(10, 3) — trend'
			},
			{ key: 'indicator:fast', label: 'SMA(50, high)' },
			{ key: 'indicator:close', label: 'close' },
			{
				key: 'indicator:prior.upper',
				label: 'Donchian(20) · upper · 1 bar ago',
				group: 'Donchian(20) · 1 bar ago — prior'
			},
			{
				key: 'indicator:prior.middle',
				label: 'Donchian(20) · middle · 1 bar ago',
				group: 'Donchian(20) · 1 bar ago — prior'
			},
			{
				key: 'indicator:prior.lower',
				label: 'Donchian(20) · lower · 1 bar ago',
				group: 'Donchian(20) · 1 bar ago — prior'
			},
			{ key: 'indicator:zero', label: '0' },
			{ key: 'literal', label: 'literal value' }
		]);
	});

	it('disambiguates repeated operand labels with the indicator id', () => {
		const labels = operandChoices([
			{ id: 'a', kind: 'ema', input: 'close', parameters: { period: 20 } },
			{ id: 'b', kind: 'ema', input: 'close', parameters: { period: 20 } }
		]).map((choice) => choice.label);
		expect(labels).toEqual(['EMA(20) — a', 'EMA(20) — b', 'literal value']);
	});

	it('offers every research template, starting with the EMA reference', () => {
		expect(STRATEGY_TEMPLATE_OPTIONS[0]?.id).toBe('ema-trend');
		expect(STRATEGY_TEMPLATE_OPTIONS.map((template) => template.id)).toEqual(
			expect.arrayContaining([
				'rsi-mean-reversion',
				'macd-trend',
				'bollinger-mean-reversion',
				'donchian-breakout',
				'supertrend-trend',
				'squeeze-breakout',
				'zscore-mean-reversion',
				'ema-trend-hold'
			])
		);
		expect(STRATEGY_TEMPLATE_OPTIONS.every((template) => template.description !== '')).toBe(true);
	});
});
