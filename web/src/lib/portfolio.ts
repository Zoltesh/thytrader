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
	padding: number
): {
	points: string;
	values: number[];
	dates: string[];
	min: number;
	max: number;
} {
	if (entries.length < 2) {
		return { points: '', values: [], dates: [], min: 0, max: 0 };
	}

	const values = entries.map((e) => Number(e.total_value.amount));
	const dates = entries.map((e) => e.as_of);
	const min = Math.min(...values);
	const max = Math.max(...values);
	const range = max - min || 1; // Avoid division by zero for flat lines
	const chartW = width - padding * 2;
	const chartH = height - padding * 2;

	const coords = values.map((value, i) => {
		const x = padding + (chartW * i) / (values.length - 1);
		const y = padding + chartH - ((value - min) / range) * chartH;
		return { x, y };
	});

	const points = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');

	return { points, values, dates, min, max };
}
