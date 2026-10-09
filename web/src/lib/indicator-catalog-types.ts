/**
 * Indicator catalog vocabulary: implemented kinds, parameter names, and the entry,
 * parameter-spec, field, category and input-mode types. Re-exported by `indicator-catalog.ts`.
 */
/** Every implemented kind, in catalog order (the historical 21 first). */
export const INDICATOR_KINDS = [
	'ema',
	'sma',
	'rsi',
	'atr',
	'volume_sma',
	'highest',
	'lowest',
	'stdev',
	'stdev_sample',
	'roc',
	'williams_r',
	'cci',
	'wma',
	'momentum',
	'mfi',
	'macd',
	'bollinger',
	'stochastic',
	'adx',
	'identity',
	'constant',
	'dema',
	'tema',
	'hma',
	'kama',
	'vwma',
	'supertrend',
	'parabolic_sar',
	'aroon',
	'ichimoku',
	'vortex',
	'linear_regression',
	'trix',
	'stochastic_rsi',
	'ppo',
	'ultimate_oscillator',
	'awesome_oscillator',
	'cmo',
	'tsi',
	'keltner',
	'donchian',
	'bollinger_percent_b',
	'bollinger_bandwidth',
	'natr',
	'choppiness',
	'historical_volatility',
	'obv',
	'cmf',
	'accumulation_distribution',
	'vwap',
	'force_index',
	'zscore',
	'percent_rank'
] as const;

export type IndicatorKindValue = (typeof INDICATOR_KINDS)[number];

/** Every parameter name any kind declares. */
export const INDICATOR_PARAMETER_NAMES = [
	'period',
	'value',
	'fast_period',
	'slow_period',
	'signal_period',
	'stdev_multiplier',
	'k_period',
	'd_period',
	'multiplier',
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
	'atr_period',
	'annualization_periods'
] as const;

export type IndicatorParameterName = (typeof INDICATOR_PARAMETER_NAMES)[number];

/**
 * Parameter object of one indicator draft. Integer parameters hold numbers and
 * decimal parameters hold exact decimal strings, as in the canonical document.
 */
export type IndicatorParameters = {
	period?: number;
	value?: string;
	fast_period?: number;
	slow_period?: number;
	signal_period?: number;
	stdev_multiplier?: string;
	k_period?: number;
	d_period?: number;
	multiplier?: string;
	step?: string;
	max_step?: string;
	tenkan_period?: number;
	kijun_period?: number;
	senkou_b_period?: number;
	rsi_period?: number;
	stoch_period?: number;
	short_period?: number;
	medium_period?: number;
	long_period?: number;
	atr_period?: number;
	annualization_periods?: number;
};

export type IndicatorField = 'open' | 'high' | 'low' | 'close' | 'volume';
export type IndicatorCategory =
	'trend' | 'momentum' | 'volatility' | 'volume' | 'statistical' | 'price';
export type IndicatorInputMode = 'configurable' | 'locked' | 'none';

export type IndicatorParameterSpec = {
	name: IndicatorParameterName;
	label: string;
	value_type: 'integer' | 'decimal';
	/** Integer bound, canonical decimal text, or null when unbounded. */
	minimum: number | string | null;
	maximum: number | string | null;
	/** Decimals that must be strictly greater than `minimum`. */
	exclusive_minimum: boolean;
	/** Builder default; null for optional parameters. */
	default: number | string | null;
	/** Omitted from the document unless the author sets it. */
	optional: boolean;
	help: string;
};

export type IndicatorCatalogEntry = {
	kind: IndicatorKindValue;
	label: string;
	category: IndicatorCategory;
	summary: string;
	inputs: IndicatorField[];
	input_mode: IndicatorInputMode;
	default_input: IndicatorField | IndicatorField[] | null;
	parameter_kind: string;
	parameters: IndicatorParameterSpec[];
	/** Cross-parameter rules such as `fast_period < slow_period`. */
	constraints: string[];
	/** Series names for multi-output kinds; empty for single-output kinds. */
	outputs: string[];
	/** Human warmup formula, for example `slow_period + signal_period - 1`. */
	warmup: string;
	default_warmup_bars: number;
	supports_timeframe: boolean;
	supports_offset: boolean;
	/** Whether the kind may read a reference instrument with `source` (ADR 0096). */
	supports_source: boolean;
};

/** Largest bar lag one declaration may request (`offset`). */
export const MAX_INDICATOR_OFFSET = 500;

/** Picker groups in display order. */
export const INDICATOR_CATEGORIES: readonly { id: IndicatorCategory; label: string }[] = [
	{ id: 'trend', label: 'Trend' },
	{ id: 'momentum', label: 'Momentum' },
	{ id: 'volatility', label: 'Volatility' },
	{ id: 'volume', label: 'Volume' },
	{ id: 'statistical', label: 'Statistical' },
	{ id: 'price', label: 'Price' }
];
