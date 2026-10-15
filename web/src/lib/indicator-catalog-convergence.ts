/**
 * Convergence warmup (ADR 0140): the bars after which a recursive indicator's seed weighs at
 * most 1e-3, mirroring `strategies/schema/indicator_convergence.py` and cross-checked against
 * the backend's examples in `indicator-catalog.spec.ts`. Exact windows (SMA, Bollinger, …)
 * converge at their first value. Re-exported by `indicator-catalog.ts`.
 */
import { isIndicatorKind, numberOf } from './indicator-catalog-entries';
import type { IndicatorKindValue } from './indicator-catalog-types';
import { indicatorWarmupBars, offsetOf } from './indicator-catalog-warmup';

const RESIDUAL_SEED_WEIGHT = 1e-3;

/** Largest `warmup_bars` a strategy may declare; a longer convergence keeps no policy. */
export const MAX_DECLARED_WARMUP_BARS = 10_000;

/** Updates after a seed until its weight is at most the residual. */
function updates(alpha: number): number {
	if (alpha >= 1) return 0;
	return Math.ceil(Math.log(RESIDUAL_SEED_WEIGHT) / Math.log(1 - alpha));
}

const ema = (period: number): number => updates(2 / (period + 1));
const wilder = (period: number): number => updates(1 / period);

type DecayRule = (parameters: object) => number;

/** Extra decay updates per recursive kind, added after the kind's minimum warmup. */
const DECAY_RULES: Partial<Record<IndicatorKindValue, DecayRule>> = {
	ema: (p) => ema(numberOf(p, 'period')),
	dema: (p) => 2 * ema(numberOf(p, 'period')),
	tema: (p) => 3 * ema(numberOf(p, 'period')),
	trix: (p) => 3 * ema(numberOf(p, 'period')),
	force_index: (p) => ema(numberOf(p, 'period')),
	macd: (p) => ema(numberOf(p, 'slow_period')) + ema(numberOf(p, 'signal_period')),
	ppo: (p) => ema(numberOf(p, 'slow_period')) + ema(numberOf(p, 'signal_period')),
	tsi: (p) =>
		ema(numberOf(p, 'long_period')) +
		ema(numberOf(p, 'short_period')) +
		ema(numberOf(p, 'signal_period')),
	kama: (p) => Math.max(1, updates((2 / (numberOf(p, 'slow_period') + 1)) ** 2)),
	rsi: (p) => wilder(numberOf(p, 'period')),
	atr: (p) => wilder(numberOf(p, 'period')),
	natr: (p) => wilder(numberOf(p, 'period')),
	adx: (p) => 2 * wilder(numberOf(p, 'period')),
	supertrend: (p) => wilder(numberOf(p, 'period')),
	stochastic_rsi: (p) => wilder(numberOf(p, 'rsi_period'))
};

/** Base bars (no lag) before one declaration's seeds have decayed. */
function baseConvergenceBars(kind: IndicatorKindValue, parameters: object): number {
	if (kind === 'keltner') {
		const period = numberOf(parameters, 'period');
		const atrPeriod = numberOf(parameters, 'atr_period');
		return Math.max(period + ema(period), atrPeriod + wilder(atrPeriod));
	}
	const minimum = indicatorWarmupBars({ kind, parameters });
	const rule = DECAY_RULES[kind];
	return rule === undefined ? minimum : minimum + rule(parameters);
}

/**
 * Closed bars one declaration needs before its seeds have converged, including its bar lag.
 * Equal to the minimum warmup for exact windows; unknown kinds need 0.
 */
export function indicatorConvergenceWarmupBars(indicator: {
	kind: string;
	parameters: object;
	offset?: unknown;
}): number {
	if (!isIndicatorKind(indicator.kind)) return 0;
	return baseConvergenceBars(indicator.kind, indicator.parameters) + offsetOf(indicator);
}
