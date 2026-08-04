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
	maintenance_kind: 'initial_backfill' | 'incremental' | null;
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

export type ChartCoordinate = {
	x: number;
	y: number;
	value: number;
	amount: string;
	date: string;
	gapBefore: boolean;
};

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

/**
 * Compute chart coordinates from history entries.
 * Returns SVG path data, min/max values, and axis labels.
 * Entries are assumed oldest-first (caller reverses if needed).
 */
export function chartData(
	entries: HistoryEntry[],
	width: number,
	height: number,
	padding: number,
	samplingIntervalSeconds = 300
): {
	points: string;
	values: number[];
	dates: string[];
	coordinates: ChartCoordinate[];
	min: number;
	max: number;
	minAmount: string;
	maxAmount: string;
} {
	if (entries.length < 2) {
		return {
			points: '',
			values: [],
			dates: [],
			coordinates: [],
			min: 0,
			max: 0,
			minAmount: '0',
			maxAmount: '0'
		};
	}

	const amounts = entries.map((e) => e.total_value.amount);
	const values = amounts.map((amount) => Number(amount));
	const dates = entries.map((e) => e.as_of);
	const min = Math.min(...values);
	const max = Math.max(...values);
	const minAmount = amounts.reduce((current, amount) =>
		compareDecimalStrings(amount, current) < 0 ? amount : current
	);
	const maxAmount = amounts.reduce((current, amount) =>
		compareDecimalStrings(amount, current) > 0 ? amount : current
	);
	const range = max - min || 1; // Avoid division by zero for flat lines
	const chartW = width - padding * 2;
	const chartH = height - padding * 2;

	const coordinates = values.map((value, i) => {
		const x = padding + (chartW * i) / (values.length - 1);
		const y = padding + chartH - ((value - min) / range) * chartH;
		const priorDate = i > 0 ? Date.parse(dates[i - 1]) : Number.NaN;
		const currentDate = Date.parse(dates[i]);
		const gapBefore =
			i > 0 &&
			Number.isFinite(priorDate) &&
			Number.isFinite(currentDate) &&
			currentDate - priorDate > samplingIntervalSeconds * 2 * 1000;
		return { x, y, value, amount: amounts[i], date: dates[i], gapBefore };
	});

	const points = coordinates
		.map((coordinate) => `${coordinate.x.toFixed(1)},${coordinate.y.toFixed(1)}`)
		.join(' ');

	return { points, values, dates, coordinates, min, max, minAmount, maxAmount };
}

export function chartSegments(data: ReturnType<typeof chartData>): string[] {
	const segments: string[] = [];
	let segment: ChartCoordinate[] = [];

	for (const coordinate of data.coordinates) {
		if (coordinate.gapBefore && segment.length > 0) {
			segments.push(
				segment.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')
			);
			segment = [];
		}
		segment.push(coordinate);
	}
	if (segment.length > 0) {
		segments.push(segment.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' '));
	}
	return segments.filter((segmentPoints) => segmentPoints.split(' ').length >= 2);
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

function compareDecimalStrings(left: string, right: string): number {
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
