import { describe, expect, it } from 'vitest';
import catalogDocument from './generated/indicator-catalog.json';
import examplesDocument from './generated/indicator-warmup-examples.json';
import {
	INDICATOR_CATALOG,
	INDICATOR_CATEGORIES,
	INDICATOR_KINDS,
	catalogEntry,
	defaultInputFor,
	defaultParameters,
	groupedIndicatorKinds,
	indicatorDisplayLabel,
	indicatorWarmupBars,
	inputMatchesKind,
	isIndicatorKind,
	operandDisplayLabel,
	parameterProblem,
	parameterProblems,
	parseIndicatorCatalog,
	searchIndicatorKinds
} from './indicator-catalog';

type WarmupExample = {
	parameters: Record<string, number | string>;
	offset: number;
	warmup_bars: number;
};

const examples = examplesDocument.warmup_examples as Record<string, WarmupExample[]>;

describe('generated indicator catalog', () => {
	it('lists exactly the TypeScript kinds, in catalog order', () => {
		expect(catalogDocument.indicators.map((entry) => entry.kind)).toEqual([...INDICATOR_KINDS]);
		expect(INDICATOR_CATALOG.map((entry) => entry.kind)).toEqual([...INDICATOR_KINDS]);
		expect(isIndicatorKind('supertrend')).toBe(true);
		expect(isIndicatorKind('chikou')).toBe(false);
	});

	it('puts every kind in a known category and fills every picker group', () => {
		const categories = new Set(INDICATOR_CATEGORIES.map((category) => category.id));
		for (const entry of INDICATOR_CATALOG) expect(categories.has(entry.category)).toBe(true);
		expect(new Set(INDICATOR_CATALOG.map((entry) => entry.category))).toEqual(categories);
		expect(groupedIndicatorKinds('').map((group) => group.label)).toEqual([
			'Trend',
			'Momentum',
			'Volatility',
			'Volume',
			'Statistical',
			'Price'
		]);
	});

	it('matches every schema-computed warmup example, offsets included', () => {
		let checked = 0;
		for (const kind of INDICATOR_KINDS) {
			const cases = examples[kind];
			expect(cases, kind).toBeDefined();
			for (const example of cases ?? []) {
				expect(
					indicatorWarmupBars({ kind, parameters: example.parameters, offset: example.offset }),
					`${kind} ${JSON.stringify(example)}`
				).toBe(example.warmup_bars);
				checked += 1;
			}
		}
		expect(checked).toBeGreaterThanOrEqual(INDICATOR_KINDS.length * 2);
	});

	it('ships valid defaults whose warmup equals the reported default warmup', () => {
		for (const entry of INDICATOR_CATALOG) {
			const parameters = defaultParameters(entry.kind);
			expect(parameterProblems(entry.kind, parameters, entry.kind), entry.kind).toEqual([]);
			expect(inputMatchesKind(entry, defaultInputFor(entry)), entry.kind).toBe(true);
			expect(indicatorWarmupBars({ kind: entry.kind, parameters }), entry.kind).toBe(
				entry.default_warmup_bars
			);
		}
	});

	it('rejects a malformed or incomplete generated document', () => {
		expect(() => parseIndicatorCatalog({ indicators: [] })).toThrow(/each of the/);
		expect(() =>
			parseIndicatorCatalog({ indicators: [{ ...catalogDocument.indicators[0], kind: 'ta_lib' }] })
		).toThrow(/indicators\[0\]\.kind/);
		expect(() => parseIndicatorCatalog(null)).toThrow(/document/);
	});
});

describe('indicator search', () => {
	it('finds kinds by name, alias, category, and summary words', () => {
		expect(searchIndicatorKinds('super')).toEqual(['supertrend']);
		expect(searchIndicatorKinds('hull')).toEqual(['hma']);
		expect(searchIndicatorKinds('sar')).toEqual(['parabolic_sar']);
		expect(searchIndicatorKinds('z')).toEqual(['zscore']);
		expect(searchIndicatorKinds('%b')).toEqual(['bollinger_percent_b']);
		expect(searchIndicatorKinds('kc')).toEqual(['keltner']);
		expect(searchIndicatorKinds('chop')).toEqual(['choppiness']);
		expect(searchIndicatorKinds('obv')).toEqual(['obv']);
		expect(searchIndicatorKinds('vwap')).toEqual(['vwap']);
		expect(searchIndicatorKinds('accumulation')).toEqual(['accumulation_distribution']);
		expect(searchIndicatorKinds('ad')).toContain('accumulation_distribution');
		expect(searchIndicatorKinds('hv')).toEqual(['historical_volatility']);
		expect(searchIndicatorKinds('bb')).toEqual([
			'bollinger',
			'bollinger_percent_b',
			'bollinger_bandwidth'
		]);
		expect(searchIndicatorKinds('volatility')).toContain('historical_volatility');
		expect(searchIndicatorKinds('volatility')).toContain('keltner');
		expect(searchIndicatorKinds('moving average')).toEqual(
			expect.arrayContaining(['ema', 'sma', 'wma', 'dema', 'tema', 'hma', 'kama', 'vwma'])
		);
		expect(searchIndicatorKinds('  ')).toHaveLength(INDICATOR_KINDS.length);
		expect(searchIndicatorKinds('no such indicator')).toEqual([]);
	});

	it('groups search results by category in picker order', () => {
		// CCI matches through its summary ("Commodity channel index …").
		expect(groupedIndicatorKinds('channel')).toEqual([
			{ category: 'momentum', label: 'Momentum', kinds: ['cci'] },
			{ category: 'volatility', label: 'Volatility', kinds: ['keltner', 'donchian'] }
		]);
	});
});

describe('parameter validation', () => {
	it('reports bounds, exclusive minimums, decimals, and optional blanks', () => {
		const supertrend = catalogEntry('supertrend');
		const [atrPeriod, multiplier] = supertrend.parameters;
		expect(parameterProblem(atrPeriod, 150, 'Indicator "st"')).toBe(
			'Indicator "st" atr period must be an integer between 2 and 100.'
		);
		expect(parameterProblem(atrPeriod, 2.5, 'Indicator "st"')).toMatch(/must be an integer/);
		expect(parameterProblem(atrPeriod, undefined, 'Indicator "st"')).toBe(
			'Indicator "st" atr period is required.'
		);
		expect(parameterProblem(multiplier, '0', 'Indicator "st"')).toBe(
			'Indicator "st" multiplier must be greater than 0 and at most 10.'
		);
		expect(parameterProblem(multiplier, 'three', 'Indicator "st"')).toBe(
			'Indicator "st" multiplier must be a plain decimal number.'
		);
		expect(parameterProblem(multiplier, '2.5', 'Indicator "st"')).toBeNull();
		const annualization = catalogEntry('historical_volatility').parameters[1];
		expect(annualization.optional).toBe(true);
		expect(parameterProblem(annualization, undefined, 'Indicator "hv"')).toBeNull();
		expect(parameterProblem(annualization, 0, 'Indicator "hv"')).toMatch(/between 1 and 525600/);
	});

	it('checks cross-parameter constraints and undeclared parameters', () => {
		expect(
			parameterProblems('macd', { fast_period: 26, slow_period: 12, signal_period: 9 }, 'MACD')
		).toEqual(['MACD MACD needs fast period < slow period.']);
		expect(
			parameterProblems(
				'ichimoku',
				{ tenkan_period: 9, kijun_period: 60, senkou_b_period: 52 },
				'I'
			)
		).toEqual(['I Ichimoku needs tenkan period < kijun period < senkou b period.']);
		expect(parameterProblems('parabolic_sar', { step: '0.3', max_step: '0.2' }, 'P')).toEqual([
			'P Parabolic SAR needs step <= max step.'
		]);
		expect(parameterProblems('parabolic_sar', { step: '0.2', max_step: '0.2' }, 'P')).toEqual([]);
		expect(parameterProblems('identity', { period: 5 }, 'X')).toEqual([
			'X OHLCV does not take period.'
		]);
	});
});

describe('readable labels', () => {
	it('renders parameters, non-close sources, clocks, offsets, and series', () => {
		expect(
			indicatorDisplayLabel({
				id: 'st',
				kind: 'supertrend',
				parameters: { period: 10, multiplier: '3' }
			})
		).toBe('Supertrend(10, 3)');
		expect(
			indicatorDisplayLabel({ id: 'h', kind: 'sma', input: 'high', parameters: { period: 50 } })
		).toBe('SMA(50, high)');
		expect(
			indicatorDisplayLabel({
				id: 'e',
				kind: 'ema',
				input: 'close',
				timeframe: '4h',
				offset: 3,
				parameters: { period: 20 }
			})
		).toBe('EMA(20) @ 4h · 3 bars ago');
		expect(
			indicatorDisplayLabel({ id: 'px', kind: 'identity', input: 'low', parameters: {} })
		).toBe('low');
		expect(
			indicatorDisplayLabel({ id: 'lvl', kind: 'constant', parameters: { value: '30' } })
		).toBe('30');
		expect(
			indicatorDisplayLabel({
				id: 'hv',
				kind: 'historical_volatility',
				input: 'close',
				parameters: { period: 20 }
			})
		).toBe('Historical volatility(20)');
		expect(
			operandDisplayLabel(
				{
					id: 'm',
					kind: 'macd',
					input: 'close',
					parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }
				},
				'signal'
			)
		).toBe('MACD(12, 26, 9) · signal');
		expect(operandDisplayLabel({ id: 'mystery', kind: 'unknown', parameters: {} })).toBe('mystery');
	});
});
