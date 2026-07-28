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

export type ChartCoordinate = {
	x: number;
	y: number;
	value: number;
	date: string;
	gapBefore: boolean;
};

export type PortfolioChange = {
	amount: number;
	percent: number | null;
	direction: 'gain' | 'loss' | 'flat';
};

export function formatUsd(amount: string): string {
	return new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	}).format(Number(amount));
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
} {
	if (entries.length < 2) {
		return { points: '', values: [], dates: [], coordinates: [], min: 0, max: 0 };
	}

	const values = entries.map((e) => Number(e.total_value.amount));
	const dates = entries.map((e) => e.as_of);
	const min = Math.min(...values);
	const max = Math.max(...values);
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
		return { x, y, value, date: dates[i], gapBefore };
	});

	const points = coordinates
		.map((coordinate) => `${coordinate.x.toFixed(1)},${coordinate.y.toFixed(1)}`)
		.join(' ');

	return { points, values, dates, coordinates, min, max };
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
	const current = Number(entries[0].total_value.amount);
	const baseline = Number(entries[entries.length - 1].total_value.amount);
	const amount = current - baseline;
	const percent = baseline === 0 ? null : (amount / baseline) * 100;
	const direction = amount > 0 ? 'gain' : amount < 0 ? 'loss' : 'flat';
	return { amount, percent, direction };
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
