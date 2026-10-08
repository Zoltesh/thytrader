/**
 * Local warmup formulas (cross-checked against the backend in `indicator-catalog.spec.ts`)
 * and bar-lag offsets. Re-exported by `indicator-catalog.ts`.
 */
import { isIndicatorKind, numberOf } from './indicator-catalog-entries';
import { MAX_INDICATOR_OFFSET, type IndicatorKindValue } from './indicator-catalog-types';

type WarmupRule = (parameters: object) => number;

const period: WarmupRule = (p) => numberOf(p, 'period');
const periodPlusOne: WarmupRule = (p) => numberOf(p, 'period') + 1;
const one: WarmupRule = () => 1;
const fastSlowSignal: WarmupRule = (p) =>
	numberOf(p, 'slow_period') + numberOf(p, 'signal_period') - 1;
const signalLine: WarmupRule = (p) => numberOf(p, 'signal_period');

/** Closed bars before every output is defined, mirroring the schema's warmup rules. */
const WARMUP_RULES: Record<IndicatorKindValue, WarmupRule> = {
	ema: period,
	sma: period,
	rsi: periodPlusOne,
	atr: period,
	volume_sma: period,
	highest: period,
	lowest: period,
	stdev: period,
	stdev_sample: period,
	roc: periodPlusOne,
	williams_r: period,
	cci: period,
	wma: period,
	momentum: periodPlusOne,
	mfi: periodPlusOne,
	macd: fastSlowSignal,
	bollinger: period,
	stochastic: (p) => numberOf(p, 'k_period') + numberOf(p, 'd_period') - 1,
	adx: (p) => 2 * numberOf(p, 'period') - 1,
	identity: one,
	constant: one,
	dema: (p) => 2 * numberOf(p, 'period') - 1,
	tema: (p) => 3 * numberOf(p, 'period') - 2,
	hma: (p) => {
		const length = numberOf(p, 'period');
		return length + Math.floor(Math.sqrt(length)) - 1;
	},
	kama: periodPlusOne,
	vwma: period,
	supertrend: period,
	parabolic_sar: () => 2,
	aroon: periodPlusOne,
	ichimoku: (p) =>
		Math.max(
			numberOf(p, 'tenkan_period'),
			numberOf(p, 'kijun_period'),
			numberOf(p, 'senkou_b_period')
		),
	vortex: periodPlusOne,
	linear_regression: period,
	trix: (p) => 3 * numberOf(p, 'period') - 1,
	stochastic_rsi: (p) =>
		numberOf(p, 'rsi_period') +
		numberOf(p, 'stoch_period') +
		numberOf(p, 'k_period') +
		numberOf(p, 'd_period') -
		2,
	ppo: fastSlowSignal,
	ultimate_oscillator: (p) => numberOf(p, 'long_period') + 1,
	awesome_oscillator: (p) => numberOf(p, 'slow_period'),
	cmo: periodPlusOne,
	tsi: (p) =>
		numberOf(p, 'long_period') + numberOf(p, 'short_period') + numberOf(p, 'signal_period') - 1,
	keltner: (p) => Math.max(numberOf(p, 'period'), numberOf(p, 'atr_period')),
	donchian: period,
	bollinger_percent_b: period,
	bollinger_bandwidth: period,
	natr: period,
	choppiness: period,
	historical_volatility: periodPlusOne,
	obv: signalLine,
	cmf: period,
	accumulation_distribution: signalLine,
	vwap: period,
	force_index: periodPlusOne,
	zscore: period,
	percent_rank: periodPlusOne
};

/** True for an integer bar lag the schema accepts (0–500). */
export function isValidOffset(value: unknown): value is number {
	return (
		typeof value === 'number' &&
		Number.isInteger(value) &&
		value >= 0 &&
		value <= MAX_INDICATOR_OFFSET
	);
}

/** Declared bar lag, or 0 when omitted or invalid. */
export function offsetOf(indicator: { offset?: unknown }): number {
	return isValidOffset(indicator.offset) ? indicator.offset : 0;
}

/**
 * Closed bars one declaration needs before every output is defined, including its
 * bar lag. Unknown kinds need 0 so they never drive the warmup requirement.
 */
export function indicatorWarmupBars(indicator: {
	kind: string;
	parameters: object;
	offset?: unknown;
}): number {
	if (!isIndicatorKind(indicator.kind)) return 0;
	return WARMUP_RULES[indicator.kind](indicator.parameters) + offsetOf(indicator);
}
