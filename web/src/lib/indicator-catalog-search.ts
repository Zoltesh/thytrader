/** Picker search over kinds, labels, categories and aliases. Re-exported by `indicator-catalog.ts`. */
import { INDICATOR_CATALOG } from './indicator-catalog-entries';
import {
	INDICATOR_CATEGORIES,
	type IndicatorCatalogEntry,
	type IndicatorCategory,
	type IndicatorKindValue
} from './indicator-catalog-types';

/** Search aliases beyond each kind's id, label, category, and summary. */
const KIND_ALIASES: Partial<Record<IndicatorKindValue, readonly string[]>> = {
	ema: ['exponential', 'moving average'],
	sma: ['simple', 'moving average'],
	wma: ['weighted', 'moving average'],
	atr: ['average true range'],
	stdev: ['standard deviation'],
	stdev_sample: ['standard deviation'],
	williams_r: ['%r', 'williams'],
	cci: ['commodity'],
	mfi: ['money flow'],
	bollinger: ['bb', 'bands'],
	adx: ['dmi', 'directional'],
	identity: ['ohlcv', 'price', 'field', 'open', 'high', 'low', 'close', 'volume'],
	constant: ['level', 'threshold'],
	dema: ['double', 'moving average'],
	tema: ['triple', 'moving average'],
	hma: ['hull', 'moving average'],
	kama: ['kaufman', 'adaptive', 'moving average'],
	vwma: ['volume weighted', 'moving average'],
	supertrend: ['super trend'],
	parabolic_sar: ['sar', 'psar', 'stop and reverse'],
	ichimoku: ['cloud', 'kumo', 'tenkan', 'kijun'],
	vortex: ['vi'],
	linear_regression: ['linreg', 'regression', 'slope'],
	stochastic_rsi: ['stochrsi', 'stoch rsi'],
	ppo: ['percentage price'],
	ultimate_oscillator: ['uo'],
	awesome_oscillator: ['ao'],
	cmo: ['chande'],
	tsi: ['true strength'],
	keltner: ['kc', 'squeeze', 'channel'],
	donchian: ['channel', 'breakout', 'turtle'],
	bollinger_percent_b: ['bb', '%b', 'percent b'],
	bollinger_bandwidth: ['bb', 'bbw', 'width', 'squeeze'],
	natr: ['normalized atr'],
	choppiness: ['chop', 'ci'],
	historical_volatility: ['hv', 'realized volatility'],
	obv: ['on balance volume', 'on-balance'],
	cmf: ['chaikin'],
	accumulation_distribution: ['ad', 'a/d', 'accumulation', 'distribution', 'adl'],
	vwap: ['volume weighted average price'],
	force_index: ['force', 'elder'],
	zscore: ['z', 'z-score', 'zscore', 'standard score'],
	percent_rank: ['percentile', 'rank']
};

function categoryLabel(category: IndicatorCategory): string {
	return INDICATOR_CATEGORIES.find((item) => item.id === category)?.label ?? category;
}

function matchesTerm(entry: IndicatorCatalogEntry, term: string): boolean {
	const label = entry.label.toLowerCase();
	if (entry.kind.includes(term) || label.includes(term)) return true;
	if (entry.kind.replaceAll('_', ' ').includes(term)) return true;
	if (categoryLabel(entry.category).toLowerCase().startsWith(term)) return true;
	const aliases = KIND_ALIASES[entry.kind] ?? [];
	if (aliases.some((alias) => alias.split(/\s+/).some((word) => word.startsWith(term)))) {
		return true;
	}
	if (aliases.some((alias) => alias.startsWith(term))) return true;
	if (term.length < 3) return false;
	return entry.summary
		.toLowerCase()
		.split(/[^a-z0-9%]+/)
		.some((word) => word.startsWith(term));
}

/**
 * Kinds matching every whitespace-separated term of `query`, in catalog order.
 * Terms match the kind id, label, category, aliases, and (from three letters)
 * words of the one-line summary. A blank query returns every kind.
 */
export function searchIndicatorKinds(query: string): IndicatorKindValue[] {
	const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
	return INDICATOR_CATALOG.filter((entry) => terms.every((term) => matchesTerm(entry, term))).map(
		(entry) => entry.kind
	);
}

/** Matching kinds grouped by category in picker order; empty groups are dropped. */
export function groupedIndicatorKinds(
	query: string
): { category: IndicatorCategory; label: string; kinds: IndicatorKindValue[] }[] {
	const matches = new Set(searchIndicatorKinds(query));
	return INDICATOR_CATEGORIES.map((category) => ({
		category: category.id,
		label: category.label,
		kinds: INDICATOR_CATALOG.filter(
			(entry) => entry.category === category.id && matches.has(entry.kind)
		).map((entry) => entry.kind)
	})).filter((group) => group.kinds.length > 0);
}
