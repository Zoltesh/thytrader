export type Money = {
	amount: string;
	currency: 'USD';
};

export type PortfolioAsset = {
	currency: string;
	name: string;
	available: string;
	hold: string;
	total: string;
	value: Money | null;
};

export type Portfolio = {
	as_of: string;
	connection: {
		provider: 'coinbase';
		status: 'connected' | 'demo';
		permissions: string[];
	};
	demo: boolean;
	total_value: Money;
	assets: PortfolioAsset[];
	unvalued_assets: string[];
};

export type ApiError = {
	detail?: {
		code?: string;
		message?: string;
	};
};

export type HistoryEntry = {
	as_of: string;
	total_value: Money;
};

export type PortfolioHistory = {
	entries: HistoryEntry[];
	range: HistoryRange;
	sampling_interval_seconds: number;
};

export type HistoryRange = '24h' | '7d' | '30d' | 'all';

export type MarketProduct = {
	product_id: string;
	base_currency: string;
	quote_currency: string;
	price_increment: string;
	base_increment: string;
	quote_increment: string;
	base_min_size: string;
	quote_min_size: string;
	trading_enabled: boolean;
};

export type MarketDataPreview = {
	as_of: string;
	product: MarketProduct;
	timeframe: '1h';
	quality: {
		candle_count: number;
		gap_count: number;
		missing_intervals: number;
		latest_completed_at: string | null;
		stale: boolean;
	};
};

export type MarketDataRange = {
	starts_at: string;
	ends_at: string;
	timeframe: '1h';
	requested_candle_count: number;
	received_candle_count: number;
	gap_count: number;
	missing_intervals: number;
	complete: boolean;
};

export type MarketDataIngestionState = {
	provider: string;
	product_id: string;
	timeframe: '1h';
	status: 'never_run' | 'running' | 'succeeded' | 'failed';
	last_attempt_at: string | null;
	last_success_at: string | null;
	requested_starts_at: string | null;
	requested_ends_at: string | null;
	fresh: boolean | null;
	enabled: boolean;
	freshness: 'current' | 'delayed' | 'stale' | 'unknown';
	coverage_status: 'complete' | 'gap_detected' | 'unavailable';
	expected_latest_boundary: string;
	next_attempt_at: string | null;
	dataset_revision: number;
	maintenance_kind: 'initial_backfill' | 'incremental' | 'prefix_backfill' | null;
	coverage: {
		starts_at: string;
		ends_at: string;
		expected_candle_count: number;
		received_candle_count: number;
		gap_count: number;
		missing_intervals: number;
		complete: boolean;
		content_fingerprint: string;
	} | null;
	failure: {
		code: string;
		message: string;
		consecutive_failures: number;
	} | null;
};

/** UTC-second line point that Lightweight Charts can plot without inventing a Y value. */
export type HonestLineValuePoint = {
	readonly time: number;
	readonly value: number;
};

/** UTC-second whitespace that occupies wall-clock space and breaks the line. */
export type HonestLineGapPoint = {
	readonly time: number;
};

export type HonestLinePoint = HonestLineValuePoint | HonestLineGapPoint;

export type PortfolioHistorySample = {
	time: number;
	amount: string;
	date: string;
	value: number;
	gapBefore: boolean;
};

export type PortfolioHistoryChartModel = {
	series: HonestLinePoint[];
	samples: PortfolioHistorySample[];
	hasGaps: boolean;
	whitespaceCount: number;
	minAmount: string;
	maxAmount: string;
};

/** Cap on inserted whitespace so a multi-year hole cannot explode the canvas. */
export const MAX_PORTFOLIO_CHART_WHITESPACE = 4000;

export type PortfolioChange = {
	amount: string;
	percent: string | null;
	direction: 'gain' | 'loss' | 'flat';
};

type DecimalParts = {
	units: bigint;
	scale: number;
};

export function formatUsd(amount: string): string {
	const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
	if (!match) throw new Error('USD amounts must be canonical decimal strings');
	const sign = match[1] === '-' ? '-$' : '$';
	const wholeDigits = match[2];
	if (!wholeDigits) throw new Error('USD amounts must include whole digits');
	const fractionDigits = match[3] ?? '';
	let whole = BigInt(wholeDigits);
	let cents = BigInt(fractionDigits.slice(0, 2).padEnd(2, '0') || '0');
	if (fractionDigits[2] !== undefined && fractionDigits[2] >= '5') cents += 1n;
	if (cents === 100n) {
		whole += 1n;
		cents = 0n;
	}
	const groupedWhole = whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
	return `${sign}${groupedWhole}.${cents.toString().padStart(2, '0')}`;
}

export function permissionLabel(permission: string): string {
	return permission.charAt(0).toUpperCase() + permission.slice(1).toLowerCase();
}

export function decimalChartGeometry(amounts: readonly string[]): {
	values: number[];
	min: number;
	max: number;
	minAmount: string;
	maxAmount: string;
	positions: number[];
} {
	/** Build finite chart-only coordinates after exact decimal range calculations. */
	if (amounts.length === 0) throw new Error('Chart geometry requires at least one decimal amount');
	const minAmount = amounts.reduce((current, amount) =>
		compareDecimalStrings(amount, current) < 0 ? amount : current
	);
	const maxAmount = amounts.reduce((current, amount) =>
		compareDecimalStrings(amount, current) > 0 ? amount : current
	);
	const values = amounts.map(decimalToFiniteGeometryValue);
	const rangeAmount = subtractDecimalStrings(maxAmount, minAmount);
	const positions = amounts.map((amount) =>
		decimalGeometryRatio(subtractDecimalStrings(amount, minAmount), rangeAmount)
	);
	return {
		values,
		min: Math.min(...values),
		max: Math.max(...values),
		minAmount,
		maxAmount,
		positions
	};
}

export function isHonestLineValuePoint(point: HonestLinePoint): point is HonestLineValuePoint {
	return 'value' in point;
}

export function utcTimestampSeconds(iso: string): number | null {
	/** Convert an ISO timestamp to a Lightweight Charts UTCTimestamp (seconds). */
	const milliseconds = Date.parse(iso);
	if (!Number.isFinite(milliseconds)) return null;
	return Math.floor(milliseconds / 1000);
}

export function isMissedSnapshotGap(
	currentDate: string | undefined,
	priorDate: string | undefined,
	samplingIntervalSeconds: number
): boolean {
	/** Detect a worker downtime hole from adjacent snapshot timestamps. */
	if (currentDate === undefined || priorDate === undefined || samplingIntervalSeconds <= 0) {
		return false;
	}
	const priorMs = Date.parse(priorDate);
	const currentMs = Date.parse(currentDate);
	return (
		Number.isFinite(priorMs) &&
		Number.isFinite(currentMs) &&
		currentMs - priorMs > samplingIntervalSeconds * 2 * 1000
	);
}

export function chartHasGaps(samples: readonly { gapBefore: boolean }[]): boolean {
	/** True when any snapshot follows a missed observation interval, including orphan dots. */
	return samples.some((sample) => sample.gapBefore);
}

const EMPTY_PORTFOLIO_CHART_MODEL: PortfolioHistoryChartModel = {
	series: [],
	samples: [],
	hasGaps: false,
	whitespaceCount: 0,
	minAmount: '0',
	maxAmount: '0'
};

/**
 * Build a Lightweight Charts series from oldest-first history entries.
 *
 * X follows wall-clock time: missed sampling intervals become whitespace bars so a
 * long outage occupies more space than nearby samples. Y is never interpolated
 * across those holes; orphan post-gap snapshots remain as valued points.
 */
export function portfolioHistoryChartModel(
	entries: readonly HistoryEntry[],
	samplingIntervalSeconds = 300
): PortfolioHistoryChartModel {
	const dated: Array<{ date: string; amount: string; time: number }> = [];
	for (const entry of entries) {
		const time = utcTimestampSeconds(entry.as_of);
		if (time === null) continue;
		dated.push({ date: entry.as_of, amount: entry.total_value.amount, time });
	}
	if (dated.length < 2) {
		return EMPTY_PORTFOLIO_CHART_MODEL;
	}

	const amounts = dated.map((entry) => entry.amount);
	const { values, minAmount, maxAmount } = decimalChartGeometry(amounts);
	const samples: PortfolioHistorySample[] = dated.map((entry, index) => ({
		time: entry.time,
		amount: entry.amount,
		date: entry.date,
		value: values[index] ?? 0,
		gapBefore: isMissedSnapshotGap(
			entry.date,
			index > 0 ? dated[index - 1]?.date : undefined,
			samplingIntervalSeconds
		)
	}));

	const gapDurationsSeconds = samples.flatMap((sample, index) => {
		if (!sample.gapBefore || index === 0) return [];
		const prior = samples[index - 1];
		if (prior === undefined) return [];
		return [Math.max(0, sample.time - prior.time)];
	});
	const stepSeconds = whitespaceStepSeconds(gapDurationsSeconds, samplingIntervalSeconds);
	const series: HonestLinePoint[] = [];
	const usedTimes = new Set<number>();
	let whitespaceCount = 0;

	for (let index = 0; index < samples.length; index += 1) {
		const sample = samples[index];
		if (sample === undefined) continue;
		if (index > 0 && sample.gapBefore) {
			const prior = samples[index - 1];
			if (prior !== undefined) {
				whitespaceCount += appendGapWhitespace(
					series,
					usedTimes,
					prior.time,
					sample.time,
					stepSeconds
				);
			}
		}
		if (usedTimes.has(sample.time)) continue;
		series.push({ time: sample.time, value: sample.value });
		usedTimes.add(sample.time);
	}

	return {
		series,
		samples,
		hasGaps: chartHasGaps(samples),
		whitespaceCount,
		minAmount,
		maxAmount
	};
}

function whitespaceStepSeconds(
	gapDurationsSeconds: readonly number[],
	samplingIntervalSeconds: number
): number {
	/** Choose a uniform gap fill so hole duration stays visible without exploding bar count. */
	const sampled = Math.max(1, samplingIntervalSeconds);
	if (gapDurationsSeconds.length === 0) return sampled;
	const totalGapSeconds = gapDurationsSeconds.reduce((sum, duration) => sum + duration, 0);
	const estimated = Math.floor(totalGapSeconds / sampled);
	if (estimated <= MAX_PORTFOLIO_CHART_WHITESPACE) return sampled;
	return Math.max(sampled, Math.ceil(totalGapSeconds / MAX_PORTFOLIO_CHART_WHITESPACE));
}

function appendGapWhitespace(
	series: HonestLinePoint[],
	usedTimes: Set<number>,
	priorTime: number,
	currentTime: number,
	stepSeconds: number
): number {
	/** Insert time-only bars between snapshots so LWC cannot draw a Y line across the hole. */
	const step = Math.max(1, stepSeconds);
	let inserted = 0;
	for (let time = priorTime + step; time < currentTime; time += step) {
		if (usedTimes.has(time)) continue;
		series.push({ time });
		usedTimes.add(time);
		inserted += 1;
	}
	if (inserted === 0) {
		const midpoint = Math.floor((priorTime + currentTime) / 2);
		if (midpoint > priorTime && midpoint < currentTime && !usedTimes.has(midpoint)) {
			series.push({ time: midpoint });
			usedTimes.add(midpoint);
			inserted = 1;
		}
	}
	return inserted;
}

export function portfolioChange(entries: HistoryEntry[]): PortfolioChange | null {
	if (entries.length < 2) {
		return null;
	}
	const current = entries[0].total_value.amount;
	const baseline = entries[entries.length - 1].total_value.amount;
	const amount = subtractDecimalStrings(current, baseline);
	const percent =
		compareDecimalStrings(baseline, '0') === 0 ? null : formatPercentChange(amount, baseline);
	const direction =
		compareDecimalStrings(amount, '0') > 0
			? 'gain'
			: compareDecimalStrings(amount, '0') < 0
				? 'loss'
				: 'flat';
	return { amount, percent, direction };
}

function parseDecimal(amount: string): DecimalParts {
	const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
	if (!match) throw new Error('Amounts must be canonical decimal strings');
	const fraction = match[3] ?? '';
	const unsignedUnits = BigInt(`${match[2]}${fraction}`);
	return {
		units: match[1] === '-' ? -unsignedUnits : unsignedUnits,
		scale: fraction.length
	};
}

export function compareDecimalStrings(left: string, right: string): number {
	const leftParts = parseDecimal(left);
	const rightParts = parseDecimal(right);
	const scale = Math.max(leftParts.scale, rightParts.scale);
	const leftUnits = leftParts.units * 10n ** BigInt(scale - leftParts.scale);
	const rightUnits = rightParts.units * 10n ** BigInt(scale - rightParts.scale);
	return leftUnits < rightUnits ? -1 : leftUnits > rightUnits ? 1 : 0;
}

function subtractDecimalStrings(left: string, right: string): string {
	const leftParts = parseDecimal(left);
	const rightParts = parseDecimal(right);
	const scale = Math.max(leftParts.scale, rightParts.scale);
	const leftUnits = leftParts.units * 10n ** BigInt(scale - leftParts.scale);
	const rightUnits = rightParts.units * 10n ** BigInt(scale - rightParts.scale);
	return formatDecimal(leftUnits - rightUnits, scale);
}

function decimalToFiniteGeometryValue(amount: string): number {
	/** Convert exact text to a bounded finite value only for non-authoritative chart geometry. */
	const { units, scale } = parseDecimal(amount);
	if (units === 0n) return 0;
	const negative = units < 0n;
	const digits = (negative ? -units : units).toString();
	const significant = Number(digits.slice(0, 15));
	const exponent = digits.length - scale - Math.min(digits.length, 15);
	const value = significant * 10 ** exponent;
	if (!Number.isFinite(value)) return negative ? -Number.MAX_VALUE : Number.MAX_VALUE;
	if (value === 0) return negative ? -Number.MIN_VALUE : Number.MIN_VALUE;
	return negative ? -value : value;
}

function decimalGeometryRatio(numerator: string, denominator: string): number {
	/** Return a finite linear position in [0, 1] from exact non-negative decimal differences. */
	const numeratorParts = parseDecimal(numerator);
	const denominatorParts = parseDecimal(denominator);
	const scale = Math.max(numeratorParts.scale, denominatorParts.scale);
	const numeratorUnits = numeratorParts.units * 10n ** BigInt(scale - numeratorParts.scale);
	const denominatorUnits = denominatorParts.units * 10n ** BigInt(scale - denominatorParts.scale);
	if (denominatorUnits <= 0n || numeratorUnits <= 0n) return 0;
	if (numeratorUnits >= denominatorUnits) return 1;
	const numeratorDigits = numeratorUnits.toString();
	const denominatorDigits = denominatorUnits.toString();
	const significantDigits = 15;
	const numeratorPrefix = Number(numeratorDigits.slice(0, significantDigits));
	const denominatorPrefix = Number(denominatorDigits.slice(0, significantDigits));
	const exponent = numeratorDigits.length - denominatorDigits.length;
	const ratio = (numeratorPrefix / denominatorPrefix) * 10 ** exponent;
	if (!Number.isFinite(ratio) || ratio <= 0) return Number.MIN_VALUE;
	return Math.min(1, ratio);
}

function formatPercentChange(amount: string, baseline: string): string {
	const amountParts = parseDecimal(amount);
	const baselineParts = parseDecimal(baseline);
	let numerator = amountParts.units * 10000n;
	let denominator = baselineParts.units;
	const scaleDifference = baselineParts.scale - amountParts.scale;
	if (scaleDifference >= 0) {
		numerator *= 10n ** BigInt(scaleDifference);
	} else {
		denominator *= 10n ** BigInt(-scaleDifference);
	}
	const negative = numerator < 0n !== denominator < 0n;
	const absoluteNumerator = numerator < 0n ? -numerator : numerator;
	const absoluteDenominator = denominator < 0n ? -denominator : denominator;
	let rounded = absoluteNumerator / absoluteDenominator;
	if ((absoluteNumerator % absoluteDenominator) * 2n >= absoluteDenominator) rounded += 1n;
	return formatFixedDecimal(negative ? -rounded : rounded, 2);
}

export function formatPercent(fraction: string): string {
	const parts = parseDecimal(fraction);
	const numerator = parts.units * 10000n;
	const denominator = 10n ** BigInt(parts.scale);
	const negative = numerator < 0n;
	const absoluteNumerator = negative ? -numerator : numerator;
	let rounded = absoluteNumerator / denominator;
	if ((absoluteNumerator % denominator) * 2n >= denominator) rounded += 1n;
	return `${formatFixedDecimal(negative ? -rounded : rounded, 2)}%`;
}

function formatFixedDecimal(units: bigint, scale: number): string {
	const sign = units < 0n ? '-' : '';
	const digits = (units < 0n ? -units : units).toString().padStart(scale + 1, '0');
	const splitAt = digits.length - scale;
	return `${sign}${digits.slice(0, splitAt)}.${digits.slice(splitAt)}`;
}

function formatDecimal(units: bigint, scale: number): string {
	if (units === 0n) return '0';
	const sign = units < 0n ? '-' : '';
	const digits = (units < 0n ? -units : units).toString().padStart(scale + 1, '0');
	if (scale === 0) return `${sign}${digits}`;
	const splitAt = digits.length - scale;
	const whole = digits.slice(0, splitAt);
	const fraction = digits.slice(splitAt).replace(/0+$/, '');
	return fraction ? `${sign}${whole}.${fraction}` : `${sign}${whole}`;
}

export function isHistoryStale(
	entries: HistoryEntry[],
	samplingIntervalSeconds: number,
	nowMilliseconds = Date.now()
): boolean {
	if (entries.length === 0 || samplingIntervalSeconds <= 0) {
		return false;
	}
	const lastSnapshotMilliseconds = Date.parse(entries[0].as_of);
	return (
		Number.isFinite(lastSnapshotMilliseconds) &&
		nowMilliseconds - lastSnapshotMilliseconds > samplingIntervalSeconds * 2 * 1000
	);
}
